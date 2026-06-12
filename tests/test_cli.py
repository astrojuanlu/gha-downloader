import pytest

from gha_downloader.cli import _count_verbosity, _filter_verbosity, build_parser


def test_count_verbosity_none():
    assert _count_verbosity(["run", "download", "12345"]) == 0


def test_count_verbosity_single():
    assert _count_verbosity(["-v", "run", "download", "12345"]) == 1


def test_count_verbosity_double():
    assert _count_verbosity(["-vv", "run", "download", "12345"]) == 2


def test_count_verbosity_triple():
    assert _count_verbosity(["-vvv", "run", "download", "12345"]) == 3


def test_count_verbosity_after_subcommand():
    assert _count_verbosity(["run", "download", "12345", "-vv"]) == 2


def test_count_verbosity_verbose_flag():
    assert _count_verbosity(["--verbose", "run", "download", "12345"]) == 1


def test_filter_verbosity_removes_v():
    assert _filter_verbosity(["-vv", "run", "download", "12345"]) == [
        "run",
        "download",
        "12345",
    ]


def test_filter_verbosity_removes_verbose():
    assert _filter_verbosity(["--verbose", "run", "download", "12345"]) == [
        "run",
        "download",
        "12345",
    ]


def test_run_download_minimal():
    parser = build_parser()
    args = parser.parse_args(["run", "download", "12345"])
    assert args.run_id == "12345"
    assert args.repo is None
    assert args.job_id is None
    assert args.dir == "./runs"
    assert args.force is False


def test_run_download_all_flags():
    parser = build_parser()
    args = parser.parse_args(
        [
            "run",
            "download",
            "12345",
            "--repo",
            "myorg/myrepo",
            "--job-id",
            "42",
            "--dir",
            "/tmp/out",
            "--force",
        ]
    )
    assert args.run_id == "12345"
    assert args.repo == "myorg/myrepo"
    assert args.job_id == 42
    assert args.dir == "/tmp/out"
    assert args.force is True


def test_run_download_missing_run_id():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["run", "download"])


def test_run_download_invalid_repo():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["run", "download", "12345", "--repo", "invalid"])


def test_run_download_invalid_repo_too_many_slashes():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["run", "download", "12345", "--repo", "a/b/c"])


def test_no_command_shows_help():
    parser = build_parser()
    args = parser.parse_args([])
    assert args.command is None


def test_run_without_subcommand():
    parser = build_parser()
    args = parser.parse_args(["run"])
    assert args.command == "run"
    assert args.run_command is None


def test_help_flag():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--help"])
