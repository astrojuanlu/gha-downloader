import argparse
import json
import logging as _logging
import re
import signal
import sys

import structlog

from . import repo as repo_module
from .downloader import (
    DownloaderError,
    download_all_jobs_from_run,
    download_artifact,
    download_job,
    get_run_info,
    list_artifacts,
    slugify,
)
from .gh import (
    GhAutoDetectError,
    GhNetworkError,
    GhNotFoundError,
    GhNotInstalledError,
    GhSpawnError,
)

logger = structlog.get_logger()

_MAX_VERBOSITY = 2


def configure_logging(verbosity: int) -> None:
    verbosity = min(verbosity, _MAX_VERBOSITY)

    if verbosity >= _MAX_VERBOSITY:
        log_level_name = "debug"
    elif verbosity >= 1:
        log_level_name = "info"
    else:
        log_level_name = "warning"

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S"),
            structlog.dev.ConsoleRenderer(
                colors=sys.stderr.isatty(),
            ),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        context_class=dict,
        cache_logger_on_first_use=True,
    )

    level = getattr(_logging, log_level_name.upper())
    _logging.root.handlers.clear()
    _logging.basicConfig(
        level=level,
        format="%(message)s",
        stream=sys.stderr,
    )


def build_download_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gha-download",
        description="Download logs for a single job from a GitHub Actions run.",
    )
    parser.add_argument(
        "run_id",
        help="Numeric workflow run ID or full Actions URL.",
    )
    parser.add_argument(
        "job_id",
        type=int,
        help="Numeric job ID.",
    )
    parser.add_argument(
        "--repo",
        help="Repository in ORG/REPO format. Auto-detected if omitted.",
        type=validate_repo_arg,
    )
    parser.add_argument(
        "--dir",
        default="./runs",
        help="Root directory for downloads (default: ./runs).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase verbosity (-v for INFO, -vv for DEBUG).",
    )
    return parser


def validate_repo_arg(value: str) -> str:
    try:
        return repo_module.validate_repo(value)
    except ValueError:
        msg = f"Invalid repository format: {value!r}. Expected ORG/REPO."
        raise argparse.ArgumentTypeError(msg) from None


def _format_size(size_bytes: int) -> str:
    mb = size_bytes / (1024 * 1024)
    if mb >= 1:
        return f"{mb:.1f} MB"
    kb = size_bytes / 1024
    if kb >= 1:
        return f"{kb:.1f} KB"
    return f"{size_bytes} B"


def _parse_run_id(raw: str) -> tuple[int, str | None]:
    try:
        return int(raw), None
    except ValueError:
        m = re.search(r"/runs/(\d+)", raw)
        if not m:
            print(f"Error: Invalid run ID: {raw!r}", file=sys.stderr)
            sys.exit(2)
        url_repo: str | None = None
        repo_match = re.search(r"github\.com/([^/]+/[^/]+)/actions/", raw)
        if repo_match:
            url_repo = repo_match.group(1)
        return int(m.group(1)), url_repo


def main_download() -> None:
    signal.signal(signal.SIGINT, lambda _signum, _frame: sys.exit(130))

    parser = build_download_parser()
    args = parser.parse_args()

    configure_logging(args.verbose)

    run_id, url_repo = _parse_run_id(args.run_id)
    if args.repo is None and url_repo is not None:
        args.repo = repo_module.validate_repo(url_repo)

    try:
        download_job(
            run_id=run_id,
            job_id=args.job_id,
            repo=args.repo,
            output_dir=args.dir,
        )
    except SystemExit:
        raise
    except GhAutoDetectError as exc:
        msg = str(exc)
        if url_repo is not None:
            msg += f" (Try --repo {url_repo})"
        print(f"Error: {msg}", file=sys.stderr)
        sys.exit(2)
    except (
        GhNotInstalledError,
        GhNotFoundError,
        DownloaderError,
    ) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)
    except (GhNetworkError, GhSpawnError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(3)
    except Exception:
        logger.exception("Internal error")
        sys.exit(1)


def build_downloader_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gha-downloader",
        description="Inspect and download GitHub Actions runs, jobs, and artifacts.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase verbosity (-v for INFO, -vv for DEBUG).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run operations")
    run_sub = run_parser.add_subparsers(dest="run_command", required=True)

    run_show = run_sub.add_parser("show", help="Show run metadata")
    run_show.add_argument("run_id", type=int, help="Numeric workflow run ID.")
    run_show.add_argument("--repo", help="Repository in ORG/REPO format.")
    run_show.add_argument(
        "--include-steps", action="store_true", help="Include step details."
    )
    run_show.add_argument(
        "--only-failed", action="store_true", help="Show only failed jobs."
    )
    run_show.add_argument(
        "--json", action="store_true", dest="json_output", help="JSON output (default)."
    )

    run_download = run_sub.add_parser("download", help="Download all job logs")
    run_download.add_argument("run_id", type=int, help="Numeric workflow run ID.")
    run_download.add_argument("--repo", help="Repository in ORG/REPO format.")
    run_download.add_argument(
        "--dir", default="./runs", help="Root directory for downloads."
    )
    run_download.add_argument(
        "--force", action="store_true", help="Overwrite existing directory."
    )

    job_parser = subparsers.add_parser("job", help="Job operations")
    job_sub = job_parser.add_subparsers(dest="job_command", required=True)

    job_download = job_sub.add_parser("download", help="Download a single job")
    job_download.add_argument("run_id", type=int, help="Numeric workflow run ID.")
    job_download.add_argument("job_id", type=int, help="Numeric job ID.")
    job_download.add_argument("--repo", help="Repository in ORG/REPO format.")
    job_download.add_argument(
        "--dir", default="./runs", help="Root directory for downloads."
    )
    job_download.add_argument(
        "--force", action="store_true", help="Overwrite existing directory."
    )

    artifact_parser = subparsers.add_parser("artifact", help="Artifact operations")
    artifact_sub = artifact_parser.add_subparsers(
        dest="artifact_command", required=True
    )

    artifact_list = artifact_sub.add_parser("list", help="List artifacts")
    artifact_list.add_argument("run_id", type=int, help="Numeric workflow run ID.")
    artifact_list.add_argument("--repo", help="Repository in ORG/REPO format.")
    artifact_list.add_argument(
        "--job-id", type=int, default=None, help="Filter by job ID."
    )
    artifact_list.add_argument(
        "--all", action="store_true", dest="show_all", help="Include expired artifacts."
    )

    artifact_download = artifact_sub.add_parser("download", help="Download artifact")
    artifact_download.add_argument("run_id", type=int, help="Numeric workflow run ID.")
    artifact_download.add_argument("artifact_name", help="Artifact name (not slug).")
    artifact_download.add_argument("--repo", help="Repository in ORG/REPO format.")
    artifact_download.add_argument(
        "--dir", default="./runs", help="Root directory for downloads."
    )

    return parser


def _cmd_run_show(args: argparse.Namespace) -> None:
    try:
        result = get_run_info(
            run_id=args.run_id,
            repo=args.repo,
            include_steps=args.include_steps,
            only_failed=args.only_failed,
        )
    except DownloaderError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)
    print(json.dumps(result, indent=2))


def _cmd_run_download(args: argparse.Namespace) -> None:
    try:
        run_dir = download_all_jobs_from_run(
            run_id=args.run_id,
            repo=args.repo,
            output_dir=args.dir,
            force=args.force,
        )
    except DownloaderError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)
    except (GhNetworkError, GhSpawnError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(3)
    print(run_dir)


def _cmd_job_download(args: argparse.Namespace) -> None:
    try:
        run_dir = download_job(
            run_id=args.run_id,
            job_id=args.job_id,
            repo=args.repo,
            output_dir=args.dir,
            force=args.force,
        )
    except DownloaderError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)
    except (GhNetworkError, GhSpawnError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(3)
    print(run_dir)


def _cmd_artifact_list(args: argparse.Namespace) -> None:
    try:
        artifacts = list_artifacts(
            run_id=args.run_id,
            repo=args.repo,
            job_id=args.job_id,
            only_available=not args.show_all,
        )
    except DownloaderError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)
    for art in artifacts:
        size = _format_size(art["size_in_bytes"])
        status = "expired" if art["expired"] else "available"
        slug = art["artifact_slug"]
        print(f"{art['name']}  {size}  {status}  slug: {slug}")


def _cmd_artifact_download(args: argparse.Namespace) -> None:
    art_slug = slugify(args.artifact_name)
    try:
        art_dir = download_artifact(
            run_id=args.run_id,
            artifact_slug=art_slug,
            repo=args.repo,
            output_dir=args.dir,
        )
    except DownloaderError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)
    except (GhNetworkError, GhSpawnError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(3)
    print(art_dir)


def main_downloader() -> None:
    signal.signal(signal.SIGINT, lambda _signum, _frame: sys.exit(130))

    parser = build_downloader_parser()
    args = parser.parse_args()

    configure_logging(args.verbose)

    if args.command == "run":
        if args.run_command == "show":
            _cmd_run_show(args)
        elif args.run_command == "download":
            _cmd_run_download(args)
    elif args.command == "job":
        if args.job_command == "download":
            _cmd_job_download(args)
    elif args.command == "artifact":
        if args.artifact_command == "list":
            _cmd_artifact_list(args)
        elif args.artifact_command == "download":
            _cmd_artifact_download(args)
