import pytest

from gha_downloader.cli import build_parser


def test_run_download_minimal():
    parser = build_parser()
    args = parser.parse_args(["run", "download", "12345"])
    assert args.run_id == "12345"
    assert args.repo is None
    assert args.job_id is None
    assert args.dir == "./runs"
    assert args.force is False
    assert args.verbose == 0


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


def test_verbose_single():
    parser = build_parser()
    args = parser.parse_args(["-v", "run", "download", "12345"])
    assert args.verbose == 1


def test_verbose_double():
    parser = build_parser()
    args = parser.parse_args(["-vv", "run", "download", "12345"])
    assert args.verbose == 2


def test_verbose_default_zero():
    parser = build_parser()
    args = parser.parse_args(["run", "download", "12345"])
    assert args.verbose == 0


def test_verbose_after_subcommand_rejected():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["run", "download", "-v", "12345"])
