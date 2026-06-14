import logging
import os
import sys
from pathlib import Path

try:
    from mcp.server.fastmcp import FastMCP
    from mcp.server.fastmcp.exceptions import ToolError
    from mcp.types import ToolAnnotations
except ImportError as exc:
    raise ImportError("Install the mcp extra: pip install gha-downloader[mcp]") from exc

import structlog

from .downloader import DownloaderError
from .downloader import download_run as _download_run
from .gh import GhError, GhNotFoundError, get_artifacts, get_run_view

mcp = FastMCP(
    "gha-downloader",
    instructions=(
        "MCP server for inspecting GitHub Actions runs. "
        "Use get_run_info to fetch metadata, list_artifacts to see artifacts, "
        "download_run to save logs/artifacts to disk, "
        "list_run_files to enumerate downloaded files, "
        "read_log to read log content, "
        "read_artifact_file to read artifact file content."
    ),
)


def _default_output_dir() -> str:
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return str(base / "gha-downloader" / "runs")


_ANN_READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
)
_ANN_DESTRUCTIVE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True
)
_ANN_LOCAL_READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)


@mcp.tool(annotations=_ANN_READ_ONLY)
def get_run_info(run_id: int, repo: str | None = None) -> dict:
    """Get metadata for a GitHub Actions run without downloading files.

    Returns run ID, name, status, conclusion, branch, commit SHA,
    trigger event, workflow name, URL, and a list of jobs.
    """
    try:
        data = get_run_view(str(run_id), repo=repo)
        return data.model_dump(mode="json")
    except GhError as exc:
        raise ToolError(str(exc)) from exc
    except DownloaderError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(annotations=_ANN_READ_ONLY)
def list_artifacts(run_id: int, repo: str | None = None) -> list[dict]:
    """List artifacts for a GitHub Actions run without downloading them.

    Returns a list of artifact records with id, name, size_in_bytes,
    and expired fields.
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
    """Download all logs and artifacts for a GitHub Actions run.

    Returns the absolute path of the run directory on success.
    """
    if output_dir is None:
        output_dir = _default_output_dir()
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

    Returns newline-separated relative paths of all files under the run
    directory.
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
def read_log(
    run_id: int,
    output_dir: str | None = None,
    job_slug: str | None = None,
    step_label: str | None = None,
) -> str:
    """Read the text content of a downloaded log file.

    When only run_id is given, returns the list of available job slugs.
    When job_slug is given without step_label, returns the full job log.
    When both job_slug and step_label are given, returns the step log.
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

    if job_slug is None:
        slugs = sorted(d.name for d in logs_dir.iterdir() if d.is_dir())
        if not slugs:
            raise ToolError(f"No job logs found for run {run_id}.")
        return "\n".join(slugs)

    job_dir = logs_dir / job_slug
    if not job_dir.is_dir():
        available = sorted(d.name for d in logs_dir.iterdir() if d.is_dir())
        raise ToolError(
            f"Job slug '{job_slug}' not found. Available: {', '.join(available)}"
        )

    if step_label is None:
        full_log = job_dir / "full.log"
        if not full_log.is_file():
            raise ToolError(f"full.log not found for job '{job_slug}'.")
        return full_log.read_text()

    step_file = job_dir / f"{step_label}.txt"
    if not step_file.is_file():
        available = sorted(
            p.stem for p in job_dir.iterdir() if p.is_file() and p.suffix == ".txt"
        )
        raise ToolError(
            f"Step '{step_label}' not found for job '{job_slug}'. "
            f"Available steps: {', '.join(available)}"
        )
    return step_file.read_text()


@mcp.tool(annotations=_ANN_LOCAL_READ_ONLY)
def read_artifact_file(
    run_id: int,
    artifact_slug: str,
    file_path: str,
    output_dir: str | None = None,
) -> str:
    """Read the text content of a file inside a downloaded artifact directory.

    Returns the file content as UTF-8 text. Returns an error for binary files.
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
