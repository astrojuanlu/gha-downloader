import argparse
import logging as _logging
import re
import signal
import sys

import structlog

from . import repo as repo_module
from .downloader import DownloaderError, download_run
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
        description="Download logs and artifacts from GitHub Actions runs.",
    )
    parser.add_argument(
        "run_id",
        help="Numeric workflow run ID or full Actions URL.",
    )
    parser.add_argument(
        "--repo",
        help="Repository in ORG/REPO format. Auto-detected if omitted.",
        type=validate_repo_arg,
    )
    parser.add_argument(
        "--job-id",
        type=int,
        help="Only download logs and artifacts for this job ID.",
    )
    parser.add_argument(
        "--dir",
        default="./runs",
        help="Root directory for downloads (default: ./runs).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing run directory.",
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


def _run_download(args: argparse.Namespace, url_repo: str | None) -> None:
    try:
        download_run(
            run_id=int(args.run_id) if isinstance(args.run_id, int) else args.run_id,
            repo=args.repo,
            job_id=args.job_id,
            output_dir=args.dir,
            force=args.force,
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


def _parse_run_id(raw: str) -> tuple[int, str | None, int | None]:
    try:
        return int(raw), None, None
    except ValueError:
        m = re.search(r"/runs/(\d+)", raw)
        if not m:
            print(f"Error: Invalid run ID: {raw!r}", file=sys.stderr)
            sys.exit(2)
        url_repo: str | None = None
        repo_match = re.search(r"github\.com/([^/]+/[^/]+)/actions/", raw)
        if repo_match:
            url_repo = repo_match.group(1)
        job_id: int | None = None
        jm = re.search(r"/job/(\d+)", raw)
        if jm:
            job_id = int(jm.group(1))
        return int(m.group(1)), url_repo, job_id


def main_download() -> None:
    signal.signal(signal.SIGINT, lambda _signum, _frame: sys.exit(130))

    parser = build_download_parser()
    args = parser.parse_args()

    configure_logging(args.verbose)

    run_id, url_repo, url_job_id = _parse_run_id(args.run_id)
    args.run_id = run_id
    if args.job_id is None and url_job_id is not None:
        args.job_id = url_job_id
    elif (
        args.job_id is not None and url_job_id is not None and args.job_id != url_job_id
    ):
        print(
            f"Warning: --job-id {args.job_id} overrides job ID {url_job_id} from URL.",
            file=sys.stderr,
        )
    if args.repo is None and url_repo is not None:
        args.repo = repo_module.validate_repo(url_repo)
    _run_download(args, url_repo)
