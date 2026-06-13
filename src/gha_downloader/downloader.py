import datetime as dt
import re
import shutil
import sys
from pathlib import Path

import structlog

from .gh import (
    GhExpiredArtifactError,
    GhNotFoundError,
    get_artifacts,
    get_job_steps,
    get_log_text,
    get_run_view,
)
from .gh import (
    download_artifact as gh_download_artifact,
)

logger = structlog.get_logger(__name__)


class DownloaderError(Exception):
    """User-input or state error in the download layer."""


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
        cmd = name[len(prefix) :]
        if cmd.startswith("'") and cmd.endswith("'"):
            cmd = cmd[1:-1]
        if cmd.startswith("sudo "):
            cmd = cmd[5:]
        parts = cmd.strip().split(None, 1)
        executable = parts[0]
        if "/" in executable:
            executable = executable.rsplit("/", 1)[-1]
        if "@" in executable:
            executable = executable.rsplit("@", 1)[0]
        name = executable
    return name


def _extract_artifact_ids(log_text: str) -> list[int]:
    """Extract artifact IDs from job log upload messages."""
    ids: set[int] = set()
    for m in re.finditer(r"Artifact ID is (\d+)", log_text):
        ids.add(int(m.group(1)))
    return sorted(ids)


def _parse_ts(ts: str) -> dt.datetime:
    normalized = ts.replace("Z", "+00:00")
    return dt.datetime.fromisoformat(normalized)


def _split_log_by_steps(log_text: str, step_times: list) -> dict[str, str]:
    step_texts: dict[str, str] = {}
    step_map: dict[int, str] = {}
    for s in step_times:
        label = f"{s.number:02d}_{slugify(s.name)}"
        step_texts[label] = ""
        step_map[s.number] = label

    current_label = next(iter(step_texts), "00_raw")
    for line in log_text.splitlines(keepends=True):
        ts_match = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?)", line)
        if ts_match:
            line_time = _parse_ts(ts_match.group(1))
            for step in step_times:
                if not step.startedAt:
                    continue
                started = _parse_ts(step.startedAt)
                if line_time < started:
                    continue
                if step.completedAt:
                    completed = _parse_ts(step.completedAt)
                    if line_time >= completed:
                        continue
                current_label = step_map.get(step.number, current_label)
                break
        step_texts.setdefault(current_label, "")
        step_texts[current_label] += line

    return step_texts


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
            raise DownloaderError(f"Job ID {job_id} not found in run {run_id}.")

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
            logger.info("artifact_ids_found", ids=artifact_ids)

        step_times = get_job_steps(repo, job.databaseId)
        step_texts = _split_log_by_steps(log_text, step_times)
        for label, text in step_texts.items():
            if not label or not text.strip():
                continue
            step_file = job_logs_dir / f"{label}.txt"
            step_file.write_text(text)
            logger.info("step_log_saved", path=str(step_file), step=label)

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

    try:
        artifacts = get_artifacts(run_id_str, repo=repo)
    except GhNotFoundError:
        logger.warning("no_artifacts_found")
        return

    if artifact_ids is not None:
        id_set = set(artifact_ids)
        artifacts = [a for a in artifacts if a.artifact_id in id_set]

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
            raise DownloaderError(
                f"Destination {run_dir} already exists. Use --force to overwrite."
            )
        logger.info("removing_existing", path=str(run_dir))
        shutil.rmtree(run_dir)

    run_dir.mkdir(parents=True, exist_ok=True)

    logger.info("fetching_run_metadata", run_id=run_id)
    run_data = get_run_view(run_id_str, repo=repo)

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
    print(f"Done: {run_dir}", file=sys.stderr)
