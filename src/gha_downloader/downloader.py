import re
import shutil
import sys
from pathlib import Path
from typing import Any

import structlog
from alive_progress import alive_bar
from ruamel.yaml import YAML

from .gh import (
    get_job_steps,
    get_log_text,
    get_run_view,
    get_run_workflow_info,
    get_workflow_yaml_content,
)

logger = structlog.get_logger(__name__)


class DownloaderError(Exception):
    """User-input or state error in the download layer."""


_GROUP_RUN_RE = re.compile(r"##\[group]run\s", re.IGNORECASE)

_ARTIFACT_ID_RE = re.compile(r"Artifact ID is (\d+)")


def _extract_artifact_ids(log_text: str) -> list[int]:
    """Extract sorted, deduplicated artifact IDs from a job log."""
    return sorted({int(m.group(1)) for m in _ARTIFACT_ID_RE.finditer(log_text)})


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


def _split_log_by_groups(
    log_text: str,
    steps: list,
    yaml_names: dict[int, str] | None = None,
) -> dict[str, str]:
    active_steps = [s for s in steps if s.conclusion != "skipped"]
    if not active_steps:
        return {}

    step_texts: dict[str, str] = {}
    labels: list[str] = []
    for s in active_steps:
        name = (yaml_names or {}).get(s.number, s.name)
        label = f"{s.number:02d}_{slugify(name)}"
        step_texts[label] = ""
        labels.append(label)

    current_idx = 0
    for line in log_text.splitlines(keepends=True):
        if _GROUP_RUN_RE.search(line) and current_idx + 1 < len(labels):
            current_idx += 1
        step_texts[labels[current_idx]] += line

    return step_texts


def _build_yaml_step_names(yaml_content: str, job_name: str) -> dict[int, str]:
    try:
        yaml = YAML()
        workflow = yaml.load(yaml_content)
        if not workflow:
            return {}
        jobs = workflow.get("jobs", {})
        if not jobs:
            return {}
        job_def = jobs.get(job_name)
        if not job_def:
            first_job = next(iter(jobs.values()), None)
            if first_job:
                job_def = first_job
        if not job_def:
            return {}
        steps = job_def.get("steps", [])
        if not steps:
            return {}
        names: dict[int, str] = {}
        for idx, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                continue
            name = step.get("name")
            if name:
                names[idx] = name
        return names
    except Exception:
        logger.debug("yaml_parse_failed", job_name=job_name)
        return {}


def _download_job_logs(
    repo: str | None,
    jobs: list,
    run_dir: Path,
    run_id: int,
    bar: Any | None = None,
) -> None:
    """Download logs for selected jobs."""
    run_id_str = str(run_id)
    wf_info = get_run_workflow_info(repo, run_id_str)
    is_reusable = bool(wf_info.referenced_workflows)

    yaml_names: dict[int, str] | None = None
    if not is_reusable:
        yaml_content = get_workflow_yaml_content(repo, wf_info.path, wf_info.head_sha)
        if yaml_content:
            first_job_name = jobs[0].name if jobs else ""
            yaml_names = _build_yaml_step_names(yaml_content, first_job_name) or None

    logger.info("downloading_logs", job_count=len(jobs))
    logs_dir = run_dir / "logs"
    logs_dir.mkdir(exist_ok=True)

    for job in jobs:
        if bar is not None:
            bar.text = job.name
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

        step_times = get_job_steps(repo, job.databaseId)
        step_texts = _split_log_by_groups(log_text, step_times, yaml_names)
        for label, text in step_texts.items():
            if not label or not text.strip():
                continue
            step_file = job_logs_dir / f"{label}.txt"
            step_file.write_text(text)
            logger.info("step_log_saved", path=str(step_file), step=label)

        if bar is not None:
            bar()


def download_run(
    run_id: int,
    repo: str | None = None,
    job_id: int | None = None,
    output_dir: str = "./runs",
    force: bool = False,
) -> None:
    run_id_str = str(run_id)
    run_dir = Path(output_dir) / run_id_str

    dir_existed_before = run_dir.exists()
    if dir_existed_before:
        if not force:
            raise DownloaderError(
                f"Destination {run_dir} already exists. Use --force to overwrite."
            )
        logger.info("removing_existing", path=str(run_dir))
        shutil.rmtree(run_dir)

    run_dir.mkdir(parents=True, exist_ok=True)

    try:
        logger.info("fetching_run_metadata", run_id=run_id)
        run_data = get_run_view(run_id_str, repo=repo)

        run_json_path = run_dir / "run.json"
        run_json_path.write_text(run_data.model_dump_json(indent=2))
        logger.info("run_metadata_saved", path=str(run_json_path))

        all_jobs = run_data.jobs or []
        if job_id is not None:
            jobs_to_download = [j for j in all_jobs if j.databaseId == job_id]
            if not jobs_to_download:
                raise DownloaderError(f"Job ID {job_id} not found in run {run_id}.")
        else:
            jobs_to_download = all_jobs

        bar_title = f"Job {job_id}" if job_id else "Downloading"
        with alive_bar(
            len(jobs_to_download),
            title=bar_title,
            file=sys.stderr,
            ctrl_c=False,
        ) as bar:
            _download_job_logs(repo, jobs_to_download, run_dir, run_id, bar=bar)
            bar.text = str(run_dir)

        logger.info("download_complete", run_id=run_id, path=str(run_dir))
    except BaseException:
        shutil.rmtree(run_dir, ignore_errors=True)
        raise
