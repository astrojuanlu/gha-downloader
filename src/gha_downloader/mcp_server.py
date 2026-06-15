import asyncio
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

from . import downloader as _downloader
from .downloader import DownloaderError
from .gh import GhError

mcp = FastMCP(
    "gha-downloader",
    instructions=(
        "Workflow for investigating a specific job:\n"
        "1. get_run_info — identify job names, IDs, and slugs\n"
        "2. download_job(run_id=<id>, job_id=<id>) — download only "
        "that job's logs (faster, avoids timeouts on large runs)\n"
        "3. list_logs(run_id=<id>) — list available job slugs with steps\n"
        "4. read_log_file(run_id=<id>, job_slug=<slug>) — read log "
        "content (or use native file-read tools at "
        "<run_dir>/logs/<job_slug>/full.log)\n"
        "\n"
        "For failure triage: download_failed_jobs(run_id=<id>) "
        "downloads only failed/in-progress jobs in one call.\n"
        "\n"
        "For artifacts: list_artifacts → download_artifact(slug) → "
        "read_artifact_file.\n"
        "\n"
        "To find errors in large logs without reading them in full, "
        "use search_log as the first step.\n"
        "\n"
        "Other tools: list_run_files."
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
_ANN_WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True
)
_ANN_LOCAL_READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[mGKHFJABCDsu]")


@mcp.tool(annotations=_ANN_READ_ONLY)
async def get_run_info(
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

    When ``only_failed=True``, only jobs whose conclusion is neither
    ``"success"`` nor ``"skipped"`` are returned. Jobs still in
    progress (``conclusion`` is ``None``) are included. This is useful
    for large matrix runs where most jobs pass and you only want to
    inspect failures.

    Args:
        run_id: Numeric workflow run ID.
        repo: Repository in ORG/REPO format. Auto-detected if omitted
            inside a git clone.
        include_steps: When ``True``, include per-step detail in each
            job entry (with ``step_label`` on non-skipped steps).
            Default ``False`` omits steps to keep the response compact.
        only_failed: When ``True``, exclude jobs with
            ``conclusion`` of ``"success"`` or ``"skipped"``. Jobs
            still in progress (``conclusion`` is ``None``) are
            included. Default ``False`` returns all jobs.

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
        return await asyncio.to_thread(
            _downloader.get_run_info,
            run_id=run_id,
            repo=repo,
            include_steps=include_steps,
            only_failed=only_failed,
        )
    except DownloaderError as exc:
        raise ToolError(str(exc)) from exc
    except GhError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(annotations=_ANN_READ_ONLY)
async def list_artifacts(  # noqa: PLR0913
    run_id: int,
    repo: str | None = None,
    job_id: int | None = None,
    only_available: bool = True,
    name_contains: str | None = None,
) -> list[dict]:
    """List artifacts for a GitHub Actions run without downloading them.

    Args:
        run_id: Numeric workflow run ID.
        repo: Repository in ORG/REPO format. Auto-detected if omitted
            inside a git clone.
        job_id: Optional job ID. When set, only artifacts uploaded by
            this job are returned (determined by parsing the job log
            for ``Artifact ID is`` messages). When ``None``, all
            run artifacts are returned.
        only_available: When ``True`` (default), expired artifacts
            are excluded from the returned list. When ``False``,
            all artifacts (including expired) are returned.
        name_contains: When set, return only artifacts whose name
            contains this substring (case-insensitive).

    Returns:
        List of artifact records with id, name, size_in_bytes,
        expired, and ``artifact_slug`` fields. The ``artifact_slug``
        can be passed directly to ``read_artifact_file``. Returns an
        empty list if the run has no artifacts or if ``job_id`` is
        set and the job uploaded no artifacts.

    Raises:
        ToolError: If the run is not found or the repo cannot be
            auto-detected.
    """
    try:
        return await asyncio.to_thread(
            _downloader.list_artifacts,
            run_id=run_id,
            repo=repo,
            job_id=job_id,
            only_available=only_available,
            name_contains=name_contains,
        )
    except DownloaderError as exc:
        raise ToolError(str(exc)) from exc
    except GhError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(annotations=_ANN_WRITE)
async def download_job(
    run_id: int,
    job_id: int,
    repo: str | None = None,
    output_dir: str | None = None,
    force: bool = False,
) -> str:
    """Download logs for a single job in a GitHub Actions run.

    Downloads only logs and ``run.json``; artifacts are not included.
    Use ``list_artifacts`` to discover available artifacts and
    ``download_artifact`` to download individual ones.

    When the job's log directory already exists and ``force`` is
    ``False``, returns the cached path immediately without
    re-downloading. Other jobs' logs in the same run directory
    are not affected.

    Args:
        run_id: Numeric workflow run ID.
        job_id: Required integer job ID to download.
        repo: Repository in ORG/REPO format. Auto-detected if omitted
            inside a git clone.
        output_dir: Root directory for downloads. Defaults to
            ``$XDG_DATA_HOME/gha-downloader/runs`` (or
            ``~/.local/share/gha-downloader/runs``).
        force: When ``True``, re-download and replace only this job's
            log directory. Other jobs' logs are not affected.

    Returns:
        The absolute path of the run directory on success. Appends
        ``(cached)`` when the job's log directory already existed.

    Raises:
        ToolError: If the run is not found or the repo cannot be
            auto-detected.
    """
    if output_dir is None:
        output_dir = _default_output_dir()
    run_dir = Path(output_dir) / str(run_id)
    try:
        await asyncio.to_thread(
            _downloader.download_job,
            run_id=run_id,
            job_id=job_id,
            repo=repo,
            output_dir=output_dir,
            force=force,
        )
    except DownloaderError as exc:
        raise ToolError(str(exc)) from exc
    except GhError as exc:
        raise ToolError(str(exc)) from exc
    return str(run_dir.resolve())


@mcp.tool(annotations=_ANN_WRITE)
async def download_artifact(
    run_id: int,
    artifact_slug: str,
    repo: str | None = None,
    output_dir: str | None = None,
    force: bool = False,
) -> str:
    """Download a single artifact by slug into the run directory.

    The run directory is created automatically if it does not already
    exist.
    Use ``list_artifacts`` to discover available ``artifact_slug``
    values.

    When ``force`` is ``False`` (default) and the artifact directory
    already exists and is non-empty, raises a ``ToolError`` directing
    the caller to use ``force=True``. When ``force`` is ``True`` and
    the artifact directory exists, removes it before re-downloading.

    Args:
        run_id: Numeric workflow run ID.
        artifact_slug: Filesystem-safe slug of the artifact (as
            returned by ``list_artifacts``).
        repo: Repository in ORG/REPO format. Auto-detected if omitted
            inside a git clone.
        output_dir: Root directory for downloads. Defaults to
            ``$XDG_DATA_HOME/gha-downloader/runs`` (or
            ``~/.local/share/gha-downloader/runs``).
        force: When ``True``, re-download even if artifact directory
            already exists (removes old content first).

    Returns:
        The absolute path of the artifact directory on success.

    Raises:
        ToolError: If the artifact slug is not found, the artifact
            has expired, or the artifact directory already exists
            and is non-empty with ``force=False``.
    """
    if output_dir is None:
        output_dir = _default_output_dir()
    try:
        art_dir = await asyncio.to_thread(
            _downloader.download_artifact,
            run_id=run_id,
            artifact_slug=artifact_slug,
            repo=repo,
            output_dir=output_dir,
            force=force,
        )
    except DownloaderError as exc:
        raise ToolError(str(exc)) from exc
    except GhError as exc:
        raise ToolError(str(exc)) from exc
    return str(art_dir)


@mcp.tool(annotations=_ANN_WRITE)
async def download_failed_jobs(
    run_id: int,
    repo: str | None = None,
    output_dir: str | None = None,
    force: bool = False,
) -> str:
    """Download logs for only the failed jobs in a GitHub Actions run.

    Identifies failed and in-progress jobs via ``get_run_info``, then
    downloads each one. If no jobs have failed, returns a message
    indicating no downloads were performed.

    Args:
        run_id: Numeric workflow run ID.
        repo: Repository in ORG/REPO format. Auto-detected if omitted
            inside a git clone.
        output_dir: Root directory for downloads. Defaults to
            ``$XDG_DATA_HOME/gha-downloader/runs`` (or
            ``~/.local/share/gha-downloader/runs``).
        force: Overwrite existing run directory if it already exists.

    Returns:
        The absolute path of the run directory followed by the list of
        downloaded job slugs. If no failed jobs, a message indicating
        no failures were found.

    Raises:
        ToolError: If the run is not found or the repo cannot be
            auto-detected.
    """
    if output_dir is None:
        output_dir = _default_output_dir()
    try:
        run_dir, job_slugs = await asyncio.to_thread(
            _downloader.download_failed_jobs,
            run_id=run_id,
            repo=repo,
            output_dir=output_dir,
            force=force,
        )
    except DownloaderError as exc:
        raise ToolError(str(exc)) from exc
    except GhError as exc:
        raise ToolError(str(exc)) from exc
    if not job_slugs:
        return f"No failed jobs found for run {run_id}. No downloads were performed."
    slug_list = ", ".join(job_slugs)
    return f"{run_dir.resolve()}\nDownloaded failed jobs: {slug_list}"


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
            "Download the run first with download_job."
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

    The first line of output is the absolute run directory path. Use
    this to construct full log file paths with the pattern
    ``<run_dir>/logs/<job_slug>/full.log`` or
    ``<run_dir>/logs/<job_slug>/<step_label>.txt``.

    Does not return log file content — use native file-read tools to
    read log files.

    Args:
        run_id: Numeric workflow run ID.
        output_dir: Root directory for downloads. Defaults to
            ``$XDG_DATA_HOME/gha-downloader/runs`` (or
            ``~/.local/share/gha-downloader/runs``).

    Returns:
        The absolute run directory path as the first line, followed by
        each job slug and its available step labels.

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
            "Download the run first with download_job."
        )

    slugs = sorted(d.name for d in logs_dir.iterdir() if d.is_dir())
    if not slugs:
        raise ToolError(f"No job logs found for run {run_id}.")
    lines: list[str] = [str(run_dir.resolve())]
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
def read_log_file(  # noqa: PLR0913
    run_id: int,
    job_slug: str,
    step_label: str | None = None,
    offset: int = 0,
    limit: int = 500,
    output_dir: str | None = None,
    raw: bool = False,
    tail: int | None = None,
) -> str:
    """Read the content of a downloaded log file.

    When ``step_label`` is ``None``, reads
    ``logs/<job_slug>/full.log``; when provided, reads
    ``logs/<job_slug>/<step_label>.txt``. Use ``list_logs`` to
    discover available ``job_slug`` and ``step_label`` values.

    When ``raw`` is ``False`` (default), ANSI escape sequences are
    stripped from the selected lines before joining.

    When ``tail`` is set, returns the last ``tail`` lines of the
    file. ``tail`` takes precedence over ``offset`` — any supplied
    ``offset`` is ignored when ``tail`` is set.

    Args:
        run_id: Numeric workflow run ID.
        job_slug: Filesystem-safe slug of the job.
        step_label: Step label within the job. When ``None``, reads
            the full job log.
        offset: 0-indexed first line to return (default 0). Ignored
            when ``tail`` is set.
        limit: Maximum number of lines to return (default 500).
        output_dir: Root directory for downloads. Defaults to
            ``$XDG_DATA_HOME/gha-downloader/runs`` (or
            ``~/.local/share/gha-downloader/runs``).
        raw: When ``False`` (default), ANSI escape sequences are
            stripped from the returned lines. When ``True``, the
            raw content is returned as-is.
        tail: When set, return the last N lines of the file.
            Takes precedence over ``offset``.

    Returns:
        A header line ``# Lines <start>–<end> of <total>``
        followed by the requested lines.

    Raises:
        ToolError: If the run is not downloaded, ``job_slug`` is
            not found, or ``step_label`` is set but the file does
            not exist.
    """
    if output_dir is None:
        output_dir = _default_output_dir()
    run_dir = Path(output_dir) / str(run_id)
    logs_dir = run_dir / "logs"

    if not logs_dir.is_dir():
        raise ToolError(
            f"No logs directory for run {run_id}. "
            "Download the run first with download_job."
        )

    job_dir = logs_dir / job_slug
    if not job_dir.is_dir():
        available = sorted(d.name for d in logs_dir.iterdir() if d.is_dir())
        raise ToolError(
            f"Job slug '{job_slug}' not found in run {run_id}. "
            f"Available slugs: {', '.join(available)}"
        )

    if step_label is None:
        target = job_dir / "full.log"
    else:
        target = job_dir / f"{step_label}.txt"

    if not target.is_file():
        step_files = sorted(
            p.stem for p in job_dir.iterdir() if p.is_file() and p.suffix == ".txt"
        )
        raise ToolError(
            f"Log file for step '{step_label}' not found for job "
            f"'{job_slug}' in run {run_id}. "
            f"Available steps: {', '.join(step_files)}"
        )

    all_lines = target.read_text(encoding="utf-8").splitlines()
    if not raw:
        all_lines = [_ANSI_ESCAPE_RE.sub("", line) for line in all_lines]
    total = len(all_lines)
    if tail is not None:
        start = max(0, total - tail)
    else:
        start = offset
    end = min(start + limit, total)
    selected = all_lines[start:end]
    header = f"# Lines {start + 1}–{end} of {total}"
    return header + "\n" + "\n".join(selected)


@mcp.tool(annotations=_ANN_LOCAL_READ_ONLY)
def read_artifact_file(  # noqa: PLR0913
    run_id: int,
    artifact_slug: str,
    file_path: str,
    output_dir: str | None = None,
    raw: bool = False,
    offset: int = 0,
    limit: int = 500,
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
        raw: When ``False`` (default), ANSI escape sequences are
            stripped from the returned text. When ``True``, the
            raw content is returned as-is.
        offset: 0-indexed first line to return (default 0).
        limit: Maximum number of lines to return (default 500).

    Returns:
        A header line ``# Lines <start>–<end> of <total>``
        followed by the requested lines (ANSI-stripped unless
        ``raw=True``).

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
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ToolError(
            f"File '{file_path}' is binary and cannot be returned as text."
        ) from None

    if not raw:
        text = _ANSI_ESCAPE_RE.sub("", text)

    all_lines = text.splitlines()
    total = len(all_lines)
    start = offset
    end = min(start + limit, total)
    selected = all_lines[start:end]
    header = f"# Lines {start + 1}–{end} of {total}"
    return header + "\n" + "\n".join(selected)


@mcp.tool(annotations=_ANN_LOCAL_READ_ONLY)
def search_log(  # noqa: PLR0913, PLR0915, PLR0912
    run_id: int,
    pattern: str,
    job_slug: str | None = None,
    step_label: str | None = None,
    output_dir: str | None = None,
    context_lines: int = 0,
    max_results: int | None = None,
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
        max_results: Maximum number of result lines to return. When
            set, collection stops once the line count reaches this
            limit and a truncation note is appended.

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
            "Download the run first with download_job."
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
        job_dir = logs_dir / job_slug
        if not job_dir.is_dir():
            raise ToolError(
                f"Job '{job_slug}' has not been downloaded for run {run_id}. "
                f"Call download_job(run_id={run_id}, job_id=<id>) first."
            )
        target = job_dir / "full.log"
        if not target.is_file():
            raise ToolError(f"full.log missing for job '{job_slug}' in run {run_id}.")
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
    total_lines = 0
    truncated = False
    for slug, path in targets:
        lines = path.read_text().splitlines()
        matches: list[str] = []
        for i, line in enumerate(lines):
            if compiled.search(line):
                start = max(0, i - context_lines)
                end = min(len(lines), i + context_lines + 1)
                for j in range(start, end):
                    clean = _ANSI_ESCAPE_RE.sub("", lines[j])
                    matches.append(f"{slug}:{j + 1}: {clean}")
                    if max_results is not None:
                        total_lines += 1
                        if total_lines >= max_results:
                            break
                if max_results is not None and total_lines >= max_results:
                    truncated = True
                    break
        if matches:
            groups.append("\n".join(matches))
        if truncated:
            break

    if not groups:
        return "No matches found."

    result = "\n\n".join(groups)
    if truncated:
        result += (
            f"\n[results truncated at {max_results} lines — "
            "narrow search with job_slug or step_label for more]"
        )
    return result


@mcp.tool(annotations=_ANN_LOCAL_READ_ONLY)
def list_artifact_files(
    run_id: int,
    artifact_slug: str,
    output_dir: str | None = None,
) -> str:
    """List files within a downloaded artifact directory.

    The artifact must have been downloaded first using
    ``download_artifact``.

    Args:
        run_id: Numeric workflow run ID.
        artifact_slug: Slug of the artifact directory (derived from
            the artifact name).
        output_dir: Root directory for downloads. Defaults to
            ``$XDG_DATA_HOME/gha-downloader/runs`` (or
            ``~/.local/share/gha-downloader/runs``).

    Returns:
        Newline-separated relative file paths within the artifact
        directory.

    Raises:
        ToolError: If the artifact directory does not exist (call
            ``download_artifact`` first).
    """
    if output_dir is None:
        output_dir = _default_output_dir()
    run_dir = Path(output_dir) / str(run_id)
    art_dir = run_dir / "artifacts" / artifact_slug

    if not art_dir.is_dir():
        raise ToolError(
            f"Artifact directory '{artifact_slug}' does not exist. "
            "Download the artifact first with download_artifact."
        )

    lines: list[str] = []
    for path in sorted(art_dir.rglob("*")):
        if path.is_file():
            lines.append(str(path.relative_to(art_dir)))
    return "\n".join(lines)


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
