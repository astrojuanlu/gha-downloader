import functools
import json
import shutil
import subprocess
import time

import pydantic
import structlog

logger = structlog.get_logger(__name__)

_NETWORK_ERROR_KEYWORDS = (
    "connection refused",
    "could not resolve host",
    "connect: network is unreachable",
    "tls handshake timeout",
    "i/o timeout",
    "request timed out",
    "no route to host",
    "connection reset",
)


class GhError(Exception):
    """Error from the gh CLI."""


class GhApiError(GhError):
    """API-level error from gh CLI (non-retriable)."""


class GhNotFoundError(GhApiError):
    """Resource not found (HTTP 404)."""


class GhExpiredArtifactError(GhApiError):
    """Artifact has expired (HTTP 410)."""


class GhNotInstalledError(GhError):
    """gh binary not found on PATH."""


class GhAutoDetectError(GhError):
    """Repository cannot be auto-detected."""


class GhNetworkError(GhError):
    """Network failure persists after all retry attempts."""


class GhSpawnError(GhError):
    """OSError on every spawn attempt."""


class RunViewData(pydantic.BaseModel):
    """Parsed output of `gh run view --json`."""

    model_config = pydantic.ConfigDict(extra="ignore")

    databaseId: int
    name: str
    status: str
    conclusion: str | None = None
    createdAt: str
    displayTitle: str
    event: str
    headBranch: str
    headSha: str
    url: str
    workflowName: str
    jobs: list[JobData] | None = None


class JobData(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="ignore")

    databaseId: int
    name: str
    status: str
    conclusion: str | None = None
    startedAt: str
    completedAt: str | None = None
    steps: list[StepData] | None = None


class StepData(pydantic.BaseModel):
    """Step info nested in JobData."""

    model_config = pydantic.ConfigDict(extra="ignore")

    name: str
    status: str
    conclusion: str | None = None
    number: int
    startedAt: str | None = None
    completedAt: str | None = None


class ArtifactData(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="ignore", populate_by_name=True)

    artifact_id: int = pydantic.Field(alias="id")
    name: str
    size_in_bytes: int
    expired: bool
    archive_download_url: str | None = None


@functools.cache
def find_gh() -> str:
    gh = shutil.which("gh")
    if gh is None:
        raise GhNotInstalledError("'gh' CLI not found. Please install GitHub CLI.")
    return gh


def run_gh(args: list[str], retries: int = 3) -> subprocess.CompletedProcess:
    gh = find_gh()
    cmd = [gh, *args]

    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        logger.debug("gh_call", cmd=cmd, attempt=attempt)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as e:
            last_exc = e
            logger.debug("gh_spawn_failed", error=str(e), attempt=attempt)
            if attempt < retries:
                time.sleep(2 ** (attempt - 1))
            continue

        if result.returncode == 0:
            return result

        stderr_lower = result.stderr.lower()

        if "no git remotes" in stderr_lower or "could not determine" in stderr_lower:
            raise GhAutoDetectError(
                "Cannot auto-detect repository. "
                "Run inside a git clone or use --repo ORG/REPO."
            )

        if "expired" in stderr_lower:
            raise GhExpiredArtifactError(result.stderr.strip())

        if "not found" in stderr_lower or "could not find" in stderr_lower:
            raise GhNotFoundError(result.stderr.strip())

        if _is_network_error(stderr_lower):
            logger.warning(
                "gh_network_error",
                attempt=attempt,
                stderr=result.stderr.strip(),
            )
            if attempt < retries:
                time.sleep(2 ** (attempt - 1))
                continue
            raise GhNetworkError(f"Network failure after {retries} attempts.")

        raise GhApiError(result.stderr.strip())

    raise GhSpawnError(f"Could not spawn gh CLI after {retries} attempts: {last_exc}")


def _build_repo_args(repo: str | None) -> list[str]:
    if repo is not None:
        return ["-R", repo]
    return []


def _api_endpoint(path: str, repo: str | None) -> str:
    if repo is not None:
        org, name = repo.split("/", 1)
        return path.replace("{owner}", org).replace("{repo}", name)
    if "{owner}" in path or "{repo}" in path:
        raise GhAutoDetectError(
            "Cannot auto-detect repository. "
            "Run inside a git clone or use --repo ORG/REPO."
        )
    return path


def get_job_steps(repo: str | None, job_id: int) -> list[StepData]:
    """Get step timing from job API (includes started_at/completed_at)."""
    endpoint = _api_endpoint(
        f"repos/{{owner}}/{{repo}}/actions/jobs/{job_id}",
        repo,
    )
    result = run_gh(["api", endpoint])
    data = json.loads(result.stdout)
    steps = []
    for s in data.get("steps", []):
        if "started_at" in s:
            s["startedAt"] = s.pop("started_at")
        if "completed_at" in s:
            s["completedAt"] = s.pop("completed_at")
        steps.append(StepData.model_validate(s))
    return steps


def get_run_view(run_id: str, repo: str | None = None) -> RunViewData:
    fields = (
        "databaseId,name,status,conclusion,createdAt,displayTitle,"
        "event,headBranch,headSha,url,workflowName,jobs"
    )
    args = ["run", "view", run_id, "--json", fields, *_build_repo_args(repo)]
    result = run_gh(args)
    data = json.loads(result.stdout)
    return RunViewData.model_validate(data)


def get_log_text(repo: str | None, job_id: int) -> str:
    endpoint = _api_endpoint(
        f"repos/{{owner}}/{{repo}}/actions/jobs/{job_id}/logs",
        repo,
    )
    result = run_gh(["api", endpoint])
    return result.stdout


def get_artifacts(run_id: str, repo: str | None = None) -> list[ArtifactData]:
    endpoint = _api_endpoint(
        f"repos/{{owner}}/{{repo}}/actions/runs/{run_id}/artifacts",
        repo,
    )
    result = run_gh(["api", endpoint, "--paginate", "--jq", ".artifacts[]"])
    artifacts = []
    for raw_line in result.stdout.splitlines():
        stripped = raw_line.strip()
        if stripped:
            artifacts.append(ArtifactData.model_validate(json.loads(stripped)))
    return artifacts


def download_artifact(
    run_id: str,
    name: str,
    output_dir: str,
    repo: str | None = None,
) -> None:
    args = [
        "run",
        "download",
        run_id,
        "-n",
        name,
        "-D",
        output_dir,
        *_build_repo_args(repo),
    ]
    run_gh(args)


def _is_network_error(stderr_lower: str) -> bool:
    return any(kw in stderr_lower for kw in _NETWORK_ERROR_KEYWORDS)
