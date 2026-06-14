import logging
import os
import re
import sys
from pathlib import Path

try:
    from mcp.server.fastmcp import FastMCP
    from mcp.server.fastmcp.exceptions import ToolError
    from mcp.types import ToolAnnotations
except ImportError as exc:
    raise ImportError("Install the mcp extra: pip install gha-downloader[mcp]") from exc

import structlog

from .downloader import DownloaderError, slugify
from .downloader import download_run as _download_run
from .gh import GhError, GhNotFoundError, get_artifacts, get_run_view

mcp = FastMCP(
    "gha-downloader",
    instructions=(
        "Workflow for investigating a specific job:\n"
        "1. get_run_info — identify job names, IDs, and slugs\n"
        "2. download_run(job_id=<id>) — download only that job's logs "
        "(faster, avoids timeouts on large runs)\n"
        "3. list_logs(run_id=<id>) — list available job slugs with steps\n"
        "4. Read log content using native file-read tools at "
        "<run_dir>/logs/<job_slug>/full.log or "
        "<run_dir>/logs/<job_slug>/<step_label>.txt\n"
        "\n"
        "To find errors in large logs without reading them in full, "
        "use search_log as the first step.\n"
        "\n"
        "Other tools: list_artifacts, list_run_files, read_artifact_file."
    ),
)


def _default_output_dir() -> str:
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return str(base / "gha-downloader" / "runs")


_ANN_READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
)
_ANN_DESTRUCTIVE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True
)
_ANN_LOCAL_READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)


@mcp.tool(annotations=_ANN_READ_ONLY)
def get_run_info(
    run_id: int,
    repo: str | None = None,
    include_steps: bool = False,
    only_failed: bool = False,
) -> dict:
    """Get metadata for a GitHub Actions run without downloading files.

    Each job entry includes a ``job_slug`` field (the filesystem-safe
    slug derived from the job display name) for use with ``list_logs``
    and ``search_log``.

    When ``include_steps=True``, each non-skipped step includes a
    ``step_label`` field matching the ``step_label`` accepted by
    ``search_log``. Note: ``step_label`` values are computed from the
    GitHub API step name and may differ from on-disk filenames when
    workflow YAML name overrides are used. Call ``list_logs`` for
    authoritative on-disk step labels.

    When ``only_failed=True``, only jobs whose conclusion is not
    ``"success"`` are returned. This is useful for large matrix runs
    where most jobs pass and you only want to inspect failures.

    Args:
        run_id: Numeric workflow run ID.
        repo: Repository in ORG/REPO format. Auto-detected if omitted
            inside a git clone.
        include_steps: When ``True``, include per-step detail in each
            job entry (with ``step_label`` on non-skipped steps).
            Default ``False`` omits steps to keep the response compact.
        only_failed: When ``True``, exclude jobs with
            ``conclusion == "success"``. Default ``False`` returns all
            jobs.

    Returns:
        Dict with run ID, name, status, conclusion, branch, commit SHA,
        trigger event, workflow name, URL, and a list of jobs (each
        with ``job_slug``; steps only when ``include_steps=True``;
        filtered to non-successful jobs when ``only_failed=True``).

    Raises:
        ToolError: If the run is not found or the repo cannot be
            auto-detected.
    """
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
                j for j in result["jobs"] if j.get("conclusion") != "success"
            ]
        return result
    except GhError as exc:
        raise ToolError(str(exc)) from exc
    except DownloaderError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(annotations=_ANN_READ_ONLY)
def list_artifacts(run_id: int, repo: str | None = None) -> list[dict]:
    """List artifacts for a GitHub Actions run without downloading them.

    Args:
        run_id: Numeric workflow run ID.
        repo: Repository in ORG/REPO format. Auto-detected if omitted
            inside a git clone.

    Returns:
        List of artifact records with id, name, size_in_bytes, and
        expired fields. Returns an empty list if the run has no
        artifacts.

    Raises:
        ToolError: If the run is not found or the repo cannot be
            auto-detected.
    """
    try:
        artifacts = get_artifacts(str(run_id), repo=repo)
        return [a.model_dump(mode="json") for a in artifacts]
    except GhNotFoundError as exc:
        raise ToolError(str(exc)) from exc
    except GhError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(annotations=_ANN_DESTRUCTIVE)
def download_run(
    run_id: int,
    repo: str | None = None,
    job_id: int | None = None,
    output_dir: str | None = None,
    force: bool = False,
) -> str:
    """Download logs and artifacts for a GitHub Actions run.

    Passing ``job_id`` restricts the download to a single job's logs
    and artifacts, which is significantly faster and avoids timeouts
    on large runs. Without ``job_id``, all jobs are downloaded.

    When the run directory already exists and ``force`` is ``False``,
    returns the cached path immediately without re-downloading.

    Args:
        run_id: Numeric workflow run ID.
        repo: Repository in ORG/REPO format. Auto-detected if omitted
            inside a git clone.
        job_id: Only download logs and artifacts for this job ID.
        output_dir: Root directory for downloads. Defaults to
            ``$XDG_DATA_HOME/gha-downloader/runs`` (or
            ``~/.local/share/gha-downloader/runs``).
        force: Overwrite existing run directory if it already exists.

    Returns:
        The absolute path of the run directory on success. Appends
        ``(cached)`` when the directory already existed.

    Raises:
        ToolError: If the run is not found or the repo cannot be
            auto-detected.
    """
    if output_dir is None:
        output_dir = _default_output_dir()
    run_dir = Path(output_dir) / str(run_id)
    if run_dir.is_dir() and not force:
        return f"{run_dir.resolve()} (cached)"
    try:
        _download_run(
            run_id=run_id,
            repo=repo,
            job_id=job_id,
            output_dir=output_dir,
            force=force,
        )
        return str((Path(output_dir) / str(run_id)).resolve())
    except DownloaderError as exc:
        raise ToolError(str(exc)) from exc
    except GhError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(annotations=_ANN_LOCAL_READ_ONLY)
def list_run_files(run_id: int, output_dir: str | None = None) -> str:
    """Enumerate downloaded files for a run (logs and artifacts).

    Args:
        run_id: Numeric workflow run ID.
        output_dir: Root directory for downloads. Defaults to
            ``$XDG_DATA_HOME/gha-downloader/runs`` (or
            ``~/.local/share/gha-downloader/runs``).

    Returns:
        Newline-separated relative paths of all files under the run
        directory.

    Raises:
        ToolError: If the run directory does not exist (run not
            downloaded yet).
    """
    if output_dir is None:
        output_dir = _default_output_dir()
    run_dir = Path(output_dir) / str(run_id)
    if not run_dir.is_dir():
        raise ToolError(
            f"Run directory {run_dir} does not exist. "
            "Download the run first with download_run."
        )

    lines: list[str] = []
    for subtree in ("logs", "artifacts"):
        subtree_dir = run_dir / subtree
        if not subtree_dir.is_dir():
            continue
        for path in sorted(subtree_dir.rglob("*")):
            if path.is_file():
                lines.append(str(path.relative_to(run_dir)))

    run_json = run_dir / "run.json"
    if run_json.is_file():
        lines.insert(0, "run.json")

    return "\n".join(lines)


@mcp.tool(annotations=_ANN_LOCAL_READ_ONLY)
def list_logs(run_id: int, output_dir: str | None = None) -> str:
    """List downloaded job logs for a run, showing slugs and step labels.

    Returns a discovery listing of job slugs and their available step
    labels. Does not return log file content — use native file-read
    tools with the path from ``download_run`` to read log files.

    Args:
        run_id: Numeric workflow run ID.
        output_dir: Root directory for downloads. Defaults to
            ``$XDG_DATA_HOME/gha-downloader/runs`` (or
            ``~/.local/share/gha-downloader/runs``).

    Returns:
        Each job slug followed by an indented line listing its
        available step labels.

    Raises:
        ToolError: If the run has not been downloaded or no job logs
            are found.
    """
    if output_dir is None:
        output_dir = _default_output_dir()
    run_dir = Path(output_dir) / str(run_id)
    logs_dir = run_dir / "logs"

    if not logs_dir.is_dir():
        raise ToolError(
            f"No logs directory for run {run_id}. "
            "Download the run first with download_run."
        )

    slugs = sorted(d.name for d in logs_dir.iterdir() if d.is_dir())
    if not slugs:
        raise ToolError(f"No job logs found for run {run_id}.")
    lines: list[str] = []
    for s in slugs:
        lines.append(s)
        step_files = sorted(
            p.stem
            for p in (logs_dir / s).iterdir()
            if p.is_file() and p.suffix == ".txt"
        )
        if step_files:
            lines.append(f"  steps: {', '.join(step_files)}")
    return "\n".join(lines)


@mcp.tool(annotations=_ANN_LOCAL_READ_ONLY)
def read_artifact_file(
    run_id: int,
    artifact_slug: str,
    file_path: str,
    output_dir: str | None = None,
) -> str:
    """Read the text content of a file inside a downloaded artifact directory.

    Args:
        run_id: Numeric workflow run ID.
        artifact_slug: Slug of the artifact directory (derived from
            the artifact name).
        file_path: Relative path of the file within the artifact
            directory.
        output_dir: Root directory for downloads. Defaults to
            ``$XDG_DATA_HOME/gha-downloader/runs`` (or
            ``~/.local/share/gha-downloader/runs``).

    Returns:
        The file content as UTF-8 text.

    Raises:
        ToolError: If the artifact directory does not exist, the file
        is not found, or the file is binary and cannot be returned as
        text.
    """
    if output_dir is None:
        output_dir = _default_output_dir()
    run_dir = Path(output_dir) / str(run_id)
    art_dir = run_dir / "artifacts" / artifact_slug

    if not art_dir.is_dir():
        available = (
            sorted(d.name for d in (run_dir / "artifacts").iterdir() if d.is_dir())
            if (run_dir / "artifacts").is_dir()
            else []
        )
        raise ToolError(
            f"Artifact directory '{artifact_slug}' not found. "
            f"Available artifacts: {', '.join(available)}"
        )

    target = art_dir / file_path
    if not target.is_file():
        available = sorted(
            str(p.relative_to(art_dir)) for p in art_dir.rglob("*") if p.is_file()
        )
        raise ToolError(
            f"File '{file_path}' not found in artifact '{artifact_slug}'. "
            f"Available files: {', '.join(available)}"
        )

    try:
        return target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ToolError(
            f"File '{file_path}' is binary and cannot be returned as text."
        ) from None


@mcp.tool(annotations=_ANN_LOCAL_READ_ONLY)
def search_log(  # noqa: PLR0913, PLR0912
    run_id: int,
    pattern: str,
    job_slug: str | None = None,
    step_label: str | None = None,
    output_dir: str | None = None,
    context_lines: int = 0,
) -> str:
    """Search downloaded log content for lines matching a regex pattern.

    Args:
        run_id: Numeric workflow run ID.
        pattern: Regular expression pattern to search for.
        job_slug: Filesystem-safe slug of the job to search. When
            omitted, searches all job logs. Use ``list_logs`` or
            ``get_run_info`` to discover available ``job_slug`` values.
        step_label: Step label within the job. When omitted with
            ``job_slug``, searches ``full.log`` for that job.
            Note: ``full.log`` is the concatenation of all step logs,
            so a match will appear once per job regardless of which
            step produced it. Specify ``step_label`` to scope the
            search to a single step file.
        output_dir: Root directory for downloads. Defaults to
            ``$XDG_DATA_HOME/gha-downloader/runs`` (or
            ``~/.local/share/gha-downloader/runs``).
        context_lines: Number of lines before and after each match
            to include (default 0).

    Returns:
        Matching lines formatted as ``<job_slug>:<line_number>: <line>``,
        grouped by job with blank-line separators. Returns
        ``No matches found.`` when no lines match.

    Raises:
        ToolError: If the regex pattern is invalid, the run has not
            been downloaded, or the specified job or step is not found.
    """
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        raise ToolError(f"Invalid regex '{pattern}': {exc}") from exc

    if output_dir is None:
        output_dir = _default_output_dir()
    run_dir = Path(output_dir) / str(run_id)
    logs_dir = run_dir / "logs"

    if not logs_dir.is_dir():
        raise ToolError(
            f"No logs directory for run {run_id}. "
            "Download the run first with download_run."
        )

    if job_slug is not None and step_label is not None:
        target = logs_dir / job_slug / f"{step_label}.txt"
        if not target.is_file():
            raise ToolError(
                f"Step file '{step_label}.txt' not found for job "
                f"'{job_slug}' in run {run_id}."
            )
        targets: list[tuple[str, Path]] = [(job_slug, target)]
    elif job_slug is not None:
        target = logs_dir / job_slug / "full.log"
        if not target.is_file():
            raise ToolError(f"full.log not found for job '{job_slug}' in run {run_id}.")
        targets = [(job_slug, target)]
    else:
        targets = sorted(
            (d.name, d / "full.log")
            for d in logs_dir.iterdir()
            if d.is_dir() and (d / "full.log").is_file()
        )
        if not targets:
            raise ToolError(f"No job logs found for run {run_id}.")

    groups: list[str] = []
    for slug, path in targets:
        lines = path.read_text().splitlines()
        matches: list[str] = []
        for i, line in enumerate(lines):
            if compiled.search(line):
                start = max(0, i - context_lines)
                end = min(len(lines), i + context_lines + 1)
                for j in range(start, end):
                    matches.append(f"{slug}:{j + 1}: {lines[j]}")
        if matches:
            groups.append("\n".join(matches))

    if not groups:
        return "No matches found."

    return "\n\n".join(groups)


def main_mcp() -> None:
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S"),
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)

    mcp.run(transport="stdio")
