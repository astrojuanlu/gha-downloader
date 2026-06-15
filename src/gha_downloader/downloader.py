import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

import structlog
from ruamel.yaml import YAML

from .gh import (
    GhError,
    get_artifacts,
    get_job_steps,
    get_log_text,
    get_run_view,
    get_run_workflow_info,
    get_workflow_yaml_content,
)
from .gh import download_artifact as _gh_download_artifact

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


def _do_download(
    dest_dir: Path,
    run_id: int,
    run_id_str: str,
    repo: str | None,
    job_id: int | None,
) -> None:
    logger.info("fetching_run_metadata", run_id=run_id)
    run_data = get_run_view(run_id_str, repo=repo)

    run_json_path = dest_dir / "run.json"
    run_json_path.write_text(run_data.model_dump_json(indent=2))
    logger.info("run_metadata_saved", path=str(run_json_path))

    all_jobs = run_data.jobs or []
    if job_id is not None:
        jobs_to_download = [j for j in all_jobs if j.databaseId == job_id]
        if not jobs_to_download:
            raise DownloaderError(f"Job ID {job_id} not found in run {run_id}.")
    else:
        jobs_to_download = all_jobs

    _download_job_logs(repo, jobs_to_download, dest_dir, run_id, bar=None)

    logger.info("download_complete", run_id=run_id, path=str(dest_dir))


def download_run(
    run_id: int,
    repo: str | None = None,
    job_id: int | None = None,
    output_dir: str = "./runs",
    force: bool = False,
) -> None:
    run_id_str = str(run_id)
    run_dir = Path(output_dir) / run_id_str

    if job_id is not None:
        run_dir.mkdir(parents=True, exist_ok=True)
        logger.info("fetching_run_metadata", run_id=run_id)
        run_data = get_run_view(run_id_str, repo=repo)
        (run_dir / "run.json").write_text(run_data.model_dump_json(indent=2))
        all_jobs = run_data.jobs or []
        matching = [j for j in all_jobs if j.databaseId == job_id]
        if not matching:
            raise DownloaderError(f"Job ID {job_id} not found in run {run_id}.")
        job = matching[0]
        job_slug = slugify(job.name)
        job_log_dir = run_dir / "logs" / job_slug
        if job_log_dir.exists():
            if not force:
                return
            shutil.rmtree(job_log_dir)
        _download_job_logs(repo, [job], run_dir, run_id, bar=None)
        return

    if run_dir.exists():
        if not force:
            raise DownloaderError(
                f"Destination {run_dir} already exists. Use --force to overwrite."
            )
        logger.info("force_redownload", run_dir=str(run_dir))
        tmp_dir = Path(tempfile.mkdtemp(dir=run_dir.parent, prefix=f"{run_id_str}.tmp"))
        try:
            _do_download(tmp_dir, run_id, run_id_str, repo, job_id)
            shutil.rmtree(run_dir)
            tmp_dir.rename(run_dir)
        except BaseException:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise
    else:
        run_dir.mkdir(parents=True, exist_ok=True)
        try:
            _do_download(run_dir, run_id, run_id_str, repo, job_id)
        except BaseException:
            shutil.rmtree(run_dir, ignore_errors=True)
            raise


def get_run_info(
    run_id: int,
    repo: str | None = None,
    include_steps: bool = False,
    only_failed: bool = False,
) -> dict:
    try:
        data = get_run_view(str(run_id), repo=repo)
        result = data.model_dump(mode="json")
        for job in result["jobs"]:
            job["job_slug"] = slugify(job["name"])
            if include_steps and job.get("steps"):
                for step in job["steps"]:
                    if step.get("conclusion") != "skipped":
                        step["step_label"] = (
                            f"{step['number']:02d}_{slugify(step['name'])}"
                        )
            if not include_steps:
                job.pop("steps", None)
        if only_failed:
            result["jobs"] = [
                j
                for j in result["jobs"]
                if j.get("conclusion") not in ("success", "skipped")
            ]
        return result
    except GhError as exc:
        raise DownloaderError(str(exc)) from exc


def list_artifacts(
    run_id: int,
    repo: str | None = None,
    job_id: int | None = None,
    only_available: bool = True,
    name_contains: str | None = None,
) -> list[dict]:
    try:
        artifacts = get_artifacts(str(run_id), repo=repo)
    except GhError as exc:
        raise DownloaderError(str(exc)) from exc
    if job_id is not None:
        try:
            log_text = get_log_text(repo, job_id)
        except GhError as exc:
            raise DownloaderError(str(exc)) from exc
        id_set = set(_extract_artifact_ids(log_text))
        artifacts = [a for a in artifacts if a.artifact_id in id_set]
    if only_available:
        artifacts = [a for a in artifacts if not a.expired]
    result = [a.model_dump(mode="json") for a in artifacts]
    for artifact in result:
        artifact["artifact_slug"] = slugify(artifact["name"])
    if name_contains:
        result = [a for a in result if name_contains.lower() in a["name"].lower()]
    return result


def download_job(
    run_id: int,
    job_id: int,
    repo: str | None = None,
    output_dir: str | None = None,
    force: bool = False,
) -> Path:
    if output_dir is None:
        raise TypeError("output_dir is required")
    run_dir = Path(output_dir) / str(run_id)
    try:
        download_run(
            run_id=run_id,
            repo=repo,
            job_id=job_id,
            output_dir=output_dir,
            force=force,
        )
    except GhError as exc:
        raise DownloaderError(str(exc)) from exc
    return run_dir


def download_all_jobs_from_run(
    run_id: int,
    repo: str | None = None,
    output_dir: str | None = None,
    force: bool = False,
) -> Path:
    if output_dir is None:
        raise TypeError("output_dir is required")
    run_dir = Path(output_dir) / str(run_id)
    if run_dir.is_dir() and not force:
        return run_dir
    try:
        download_run(
            run_id=run_id,
            repo=repo,
            job_id=None,
            output_dir=output_dir,
            force=force,
        )
    except GhError as exc:
        raise DownloaderError(str(exc)) from exc
    return run_dir


def download_artifact(
    run_id: int,
    artifact_slug: str,
    repo: str | None = None,
    output_dir: str | None = None,
    force: bool = False,
) -> Path:
    if output_dir is None:
        raise TypeError("output_dir is required")
    run_dir = Path(output_dir) / str(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        artifacts = get_artifacts(str(run_id), repo=repo)
    except GhError as exc:
        raise DownloaderError(str(exc)) from exc
    slug_map = {slugify(a.name): a for a in artifacts}
    if artifact_slug not in slug_map:
        available = sorted(slug_map)
        raise DownloaderError(
            f"Artifact slug '{artifact_slug}' not found. "
            f"Available slugs: {', '.join(available)}"
        )
    artifact = slug_map[artifact_slug]
    if artifact.expired:
        raise DownloaderError(
            f"Artifact '{artifact.name}' (slug: {artifact_slug}) has expired."
        )
    art_dir = run_dir / "artifacts" / artifact_slug
    if art_dir.is_dir():
        if force:
            shutil.rmtree(art_dir)
        elif any(art_dir.iterdir()):
            raise DownloaderError(
                f"Artifact directory '{artifact_slug}' already exists "
                f"and is non-empty. Pass force=True to re-download."
            )
    art_dir.mkdir(parents=True, exist_ok=True)
    try:
        _gh_download_artifact(
            str(run_id),
            artifact.name,
            str(art_dir),
            repo=repo,
        )
    except GhError as exc:
        raise DownloaderError(str(exc)) from exc
    return art_dir.resolve()


def download_failed_jobs(
    run_id: int,
    repo: str | None = None,
    output_dir: str | None = None,
    force: bool = False,
) -> tuple[Path, list[str]]:
    if output_dir is None:
        raise TypeError("output_dir is required")
    run_dir = Path(output_dir) / str(run_id)
    info = get_run_info(run_id, repo=repo, only_failed=True)
    failed_jobs = info.get("jobs", [])
    if not failed_jobs:
        return run_dir, []
    job_slugs: list[str] = []
    for job in failed_jobs:
        job_id = job.get("databaseId")
        if job_id is None:
            continue
        download_run(
            run_id=run_id,
            repo=repo,
            job_id=job_id,
            output_dir=output_dir,
            force=force,
        )
        job_slugs.append(job.get("job_slug", ""))
    return run_dir, job_slugs
