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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gha-downloader",
        description="Download logs and artifacts from GitHub Actions runs.",
        epilog="Use -v for INFO, -vv for DEBUG verbosity. Must precede the subcommand.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase verbosity (-v for INFO, -vv for DEBUG).",
    )
    subparsers = parser.add_subparsers(dest="command")
    run_parser = subparsers.add_parser("run", help="Work with workflow runs.")
    run_subparsers = run_parser.add_subparsers(dest="run_command")
    download_parser = run_subparsers.add_parser(
        "download", help="Download run logs and artifacts."
    )
    download_parser.add_argument(
        "run_id",
        help="Numeric workflow run ID or full Actions URL.",
    )
    download_parser.add_argument(
        "--repo",
        help="Repository in ORG/REPO format. Auto-detected if omitted.",
        type=validate_repo_arg,
    )
    download_parser.add_argument(
        "--job-id",
        type=int,
        help="Only download logs and artifacts for this job ID.",
    )
    download_parser.add_argument(
        "--dir",
        default="./runs",
        help="Root directory for downloads (default: ./runs).",
    )
    download_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing run directory.",
    )
    return parser


def validate_repo_arg(value: str) -> str:
    try:
        return repo_module.validate_repo(value)
    except ValueError:
        msg = f"Invalid repository format: {value!r}. Expected ORG/REPO."
        raise argparse.ArgumentTypeError(msg) from None


def main() -> None:
    signal.signal(signal.SIGINT, lambda _signum, _frame: sys.exit(130))

    parser = build_parser()
    args = parser.parse_args()

    configure_logging(args.verbose)

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    if args.command == "run" and args.run_command is None:
        parser.parse_args(["run", "--help"])
        sys.exit(0)

    if args.command == "run" and args.run_command == "download":
        try:
            run_id = int(args.run_id)
        except ValueError:
            m = re.search(r"/runs/(\d+)", args.run_id)
            if not m:
                print(
                    f"Error: Invalid run ID: {args.run_id!r}",
                    file=sys.stderr,
                )
                sys.exit(2)
            run_id = int(m.group(1))
            if args.job_id is None:
                jm = re.search(r"/job/(\d+)", args.run_id)
                if jm:
                    args.job_id = int(jm.group(1))

        try:
            download_run(
                run_id=run_id,
                repo=args.repo,
                job_id=args.job_id,
                output_dir=args.dir,
                force=args.force,
            )
        except SystemExit:
            raise
        except (
            GhNotInstalledError,
            GhAutoDetectError,
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
