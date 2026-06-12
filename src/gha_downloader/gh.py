import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

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


class RunViewData(pydantic.BaseModel):
    """Parsed output of `gh run view --json`."""

    model_config = pydantic.ConfigDict(extra="allow")

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
    """Job info nested in RunViewData."""

    model_config = pydantic.ConfigDict(extra="allow")

    databaseId: int
    name: str
    status: str
    conclusion: str | None = None
    startedAt: str
    completedAt: str | None = None
    steps: list[StepData] | None = None


class StepData(pydantic.BaseModel):
    """Step info nested in JobData."""

    model_config = pydantic.ConfigDict(extra="allow")

    name: str
    status: str
    conclusion: str | None = None
    number: int


class ArtifactData(pydantic.BaseModel):
    """Artifact info from gh api."""

    model_config = pydantic.ConfigDict(extra="allow")

    id: int
    name: str
    size_in_bytes: int
    expired: bool
    archive_download_url: str | None = None


def find_gh() -> str:
    gh = shutil.which("gh")
    if gh is None:
        print(
            "Error: 'gh' CLI not found. Please install GitHub CLI.",
            file=sys.stderr,
        )
        sys.exit(2)
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
            print(
                f"Error: Network failure after {retries} attempts.",
                file=sys.stderr,
            )
            sys.exit(3)

        raise GhApiError(result.stderr.strip())

    if last_exc is not None:
        print(
            f"Error: Could not spawn gh CLI after {retries} attempts: {last_exc}",
            file=sys.stderr,
        )
        sys.exit(3)
    sys.exit(3)


def _build_repo_args(repo: str | None) -> list[str]:
    if repo is not None:
        return ["-R", repo]
    return []


def _api_endpoint(path: str, repo: str | None) -> str:
    if repo is not None:
        org, name = repo.split("/", 1)
        return path.replace("{owner}", org).replace("{repo}", name)
    return path


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
        "repos/{owner}/{repo}/actions/jobs/{job_id}/logs".replace(
            "{job_id}", str(job_id)
        ),
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


def download_artifact_zip(
    repo: str | None,
    artifact_id: str,
    output_dir: str,
) -> None:
    """Download a single artifact by ID and extract into output_dir."""
    endpoint = _api_endpoint(
        f"repos/{{owner}}/{{repo}}/actions/artifacts/{artifact_id}/zip",
        repo,
    )
    gh = find_gh()
    cmd = [gh, "api", endpoint]
    result = subprocess.run(
        cmd,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        stderr_text = result.stderr
        if isinstance(stderr_text, bytes):
            stderr_text = stderr_text.decode(errors="replace")
        stderr_lower = stderr_text.lower()
        if "expired" in stderr_lower:
            raise GhExpiredArtifactError(stderr_text)
        if "not found" in stderr_lower:
            raise GhNotFoundError(stderr_text)
        raise GhApiError(stderr_text)

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tf:
        tf.write(result.stdout)
        tmp_path = tf.name

    try:
        os.makedirs(output_dir, exist_ok=True)
        with zipfile.ZipFile(tmp_path) as zf:
            zf.extractall(output_dir)
    finally:
        os.unlink(tmp_path)


def _is_network_error(stderr_lower: str) -> bool:
    return any(kw in stderr_lower for kw in _NETWORK_ERROR_KEYWORDS)
