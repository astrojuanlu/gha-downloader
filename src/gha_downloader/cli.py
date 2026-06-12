import argparse
import logging as _logging
import signal
import sys

import structlog

from . import repo as repo_module
from .downloader import download_run

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
                colors=True,
                force_colors=True,
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


def _count_verbosity(raw_args: list[str]) -> int:
    count = 0
    for arg in raw_args:
        if arg in ("-v", "--verbose"):
            count += 1
        elif arg.startswith("-v") and set(arg[1:]) == {"v"}:
            count += len(arg) - 1
    return count


def _filter_verbosity(raw_args: list[str]) -> list[str]:
    return [
        a for a in raw_args if not (a == "--verbose" or set(a.lstrip("-")) == {"v"})
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gha-downloader",
        description="Download logs and artifacts from GitHub Actions runs.",
    )
    subparsers = parser.add_subparsers(dest="command")
    run_parser = subparsers.add_parser("run", help="Work with workflow runs.")
    run_subparsers = run_parser.add_subparsers(dest="run_command")
    download_parser = run_subparsers.add_parser(
        "download", help="Download run logs and artifacts."
    )
    download_parser.add_argument("run_id", help="Numeric workflow run ID.")
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

    raw_args = sys.argv[1:]
    verbosity = _count_verbosity(raw_args)
    filtered_args = _filter_verbosity(raw_args)

    configure_logging(verbosity)

    parser = build_parser()
    args = parser.parse_args(filtered_args)

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    if args.command == "run" and args.run_command is None:
        parser.parse_args(["run", "--help"])
        sys.exit(0)

    if args.command == "run" and args.run_command == "download":
        try:
            download_run(
                run_id=int(args.run_id),
                repo=args.repo,
                job_id=args.job_id,
                output_dir=args.dir,
                force=args.force,
            )
        except SystemExit:
            raise
        except BaseException:
            logger.exception("Internal error")
            sys.exit(1)
