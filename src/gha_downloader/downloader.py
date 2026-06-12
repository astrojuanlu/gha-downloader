import re
import shutil
import sys
from pathlib import Path

import structlog

from .gh import (
    GhExpiredArtifactError,
    GhNotFoundError,
    download_artifact_zip,
    get_artifacts,
    get_log_text,
    get_run_view,
)
from .gh import (
    download_artifact as gh_download_artifact,
)

logger = structlog.get_logger(__name__)


def slugify(name: str) -> str:
    """Convert a name to a filesystem-safe slug."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name)
    slug = slug.strip("-")
    slug = re.sub(r"-+", "-", slug)
    return slug.lower() if slug else "unnamed"


def _clean_step_name(raw: str) -> str:
    """Shorten raw ##[group] names from the runner log."""
    name = raw.strip()
    prefix = "Run "
    if name.startswith(prefix):
        name = name[len(prefix) :]
        if name.startswith("'") and name.endswith("'"):
            name = name[1:-1]
    max_len = 60
    if len(name) > max_len:
        name = name[: max_len - 3] + "..."
    return name


def split_log(log_text: str) -> list[tuple[str, str]]:
    """Split job log into (step_name, step_text) pairs on ##[group] markers."""
    if "##[group]" not in log_text:
        return [("raw", log_text)]

    steps: list[tuple[str, str]] = []
    current_name = "pre"
    current_lines: list[str] = []

    for line in log_text.splitlines(keepends=True):
        group_match = re.search(r"##\[group\](.*)", line)
        if group_match:
            if current_lines:
                steps.append((current_name, "".join(current_lines)))
            current_name = _clean_step_name(group_match.group(1))
            current_lines = []
        else:
            current_lines.append(line)

    if current_lines or steps:
        steps.append((current_name, "".join(current_lines)))

    return steps


def _extract_artifact_ids(log_text: str) -> list[int]:
    """Extract artifact IDs from job log upload messages."""
    ids: set[int] = set()
    for m in re.finditer(r"Artifact ID is (\d+)", log_text):
        ids.add(int(m.group(1)))
    return sorted(ids)


def _download_job_logs(
    repo: str | None,
    job_id: int | None,
    jobs: list,
    run_dir: Path,
    run_id: int,
) -> list[int]:
    """Download logs for selected jobs. Returns artifact IDs found in logs."""
    if job_id is not None:
        jobs = [j for j in jobs if j.databaseId == job_id]
        if not jobs:
            print(
                f"Error: Job ID {job_id} not found in run {run_id}.",
                file=sys.stderr,
            )
            sys.exit(2)

    logger.info("downloading_logs", job_count=len(jobs))
    logs_dir = run_dir / "logs"
    logs_dir.mkdir(exist_ok=True)

    all_artifact_ids: list[int] = []

    for job in jobs:
        job_slug = slugify(job.name)
        job_logs_dir = logs_dir / job_slug
        job_logs_dir.mkdir(exist_ok=True)

        logger.info(
            "fetching_job_log",
            job_id=job.databaseId,
            job_name=job.name,
        )
        log_text = get_log_text(repo, job.databaseId)

        (job_logs_dir / "full.log").write_text(log_text)

        artifact_ids = _extract_artifact_ids(log_text)
        all_artifact_ids.extend(artifact_ids)
        if artifact_ids:
            logger.info(
                "artifact_ids_found",
                ids=artifact_ids,
            )

        steps = split_log(log_text)
        width = len(str(len(steps)))
        for step_idx, (step_name, step_text) in enumerate(steps, start=1):
            step_slug = slugify(step_name)
            step_file = job_logs_dir / f"{step_idx:0{width}d}_{step_slug}.txt"
            step_file.write_text(step_text)
            logger.info(
                "step_log_saved",
                path=str(step_file),
                step=step_name,
            )

    return all_artifact_ids


def _download_artifacts(
    repo: str | None,
    run_id_str: str,
    run_dir: Path,
    artifact_ids: list[int] | None = None,
) -> None:
    logger.info("downloading_artifacts")
    artifacts_dir = run_dir / "artifacts"
    artifacts_dir.mkdir(exist_ok=True)

    if artifact_ids is not None:
        for art_id in artifact_ids:
            logger.info("downloading_artifact_by_id", id=art_id)
            art_dir = artifacts_dir / str(art_id)
            art_dir.mkdir(exist_ok=True)
            try:
                download_artifact_zip(repo, str(art_id), str(art_dir))
            except GhExpiredArtifactError:
                print(
                    f"Warning: Artifact {art_id} has expired.",
                    file=sys.stderr,
                )
                (art_dir / ".expired").touch()
                continue
            except GhNotFoundError:
                logger.warning("artifact_not_found", id=art_id)
                continue
        return

    try:
        artifacts = get_artifacts(run_id_str, repo=repo)
    except GhNotFoundError:
        logger.warning("no_artifacts_found")
        return

    for artifact in artifacts:
        art_slug = slugify(artifact.name)
        art_dir = artifacts_dir / art_slug

        if artifact.expired:
            print(
                f"Warning: Artifact '{artifact.name}' has expired.",
                file=sys.stderr,
            )
            art_dir.mkdir(exist_ok=True)
            (art_dir / ".expired").touch()
            continue

        logger.info("downloading_artifact", name=artifact.name)
        art_dir.mkdir(exist_ok=True)
        try:
            gh_download_artifact(run_id_str, artifact.name, str(art_dir), repo=repo)
        except GhExpiredArtifactError:
            print(
                f"Warning: Artifact '{artifact.name}' has expired.",
                file=sys.stderr,
            )
            (art_dir / ".expired").touch()
            continue


def download_run(
    run_id: int,
    repo: str | None = None,
    job_id: int | None = None,
    output_dir: str = "./runs",
    force: bool = False,
) -> None:
    run_id_str = str(run_id)
    run_dir = Path(output_dir) / run_id_str

    if run_dir.exists():
        if not force:
            print(
                f"Error: Destination {run_dir} already exists. "
                "Use --force to overwrite.",
                file=sys.stderr,
            )
            sys.exit(2)
        logger.info("removing_existing", path=str(run_dir))
        shutil.rmtree(run_dir)

    run_dir.mkdir(parents=True, exist_ok=True)

    logger.info("fetching_run_metadata", run_id=run_id)
    try:
        run_data = get_run_view(run_id_str, repo=repo)
    except GhNotFoundError:
        print(f"Error: Run {run_id} not found.", file=sys.stderr)
        sys.exit(2)

    run_json_path = run_dir / "run.json"
    run_json_path.write_text(run_data.model_dump_json(indent=2))
    logger.info("run_metadata_saved", path=str(run_json_path))

    artifact_ids = _download_job_logs(
        repo, job_id, run_data.jobs or [], run_dir, run_id
    )
    if job_id is not None:
        if artifact_ids:
            _download_artifacts(repo, run_id_str, run_dir, artifact_ids)
    else:
        _download_artifacts(repo, run_id_str, run_dir)

    logger.info("download_complete", run_id=run_id, path=str(run_dir))
