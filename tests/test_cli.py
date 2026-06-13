import pytest

import gha_downloader.cli as cli_mod
from gha_downloader.cli import _parse_run_id, build_parser


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


def test_url_repo_inference(monkeypatch):
    captured_repo: list[str | None] = []

    def fake_download_run(
        run_id, repo=None, job_id=None, output_dir="./runs", force=False
    ):
        captured_repo.append(repo)

    monkeypatch.setattr(cli_mod, "download_run", fake_download_run)
    monkeypatch.setattr(
        "sys.argv",
        [
            "gha-downloader",
            "run",
            "download",
            "https://github.com/myorg/myrepo/actions/runs/12345",
        ],
    )
    cli_mod.main()
    assert captured_repo[0] == "myorg/myrepo"


def test_url_repo_explicit_overrides(monkeypatch):
    captured_repo: list[str | None] = []

    def fake_download_run(
        run_id, repo=None, job_id=None, output_dir="./runs", force=False
    ):
        captured_repo.append(repo)

    monkeypatch.setattr(cli_mod, "download_run", fake_download_run)
    monkeypatch.setattr(
        "sys.argv",
        [
            "gha-downloader",
            "run",
            "download",
            "https://github.com/other/repo/actions/runs/12345",
            "--repo",
            "explicit/repo",
        ],
    )
    cli_mod.main()
    assert captured_repo[0] == "explicit/repo"


def test_parse_run_id_url_with_job_and_query():
    run_id, url_repo, url_job_id = _parse_run_id(
        "https://github.com/org/repo/actions/runs/12345/job/999?pr=354"
    )
    assert run_id == 12345
    assert url_repo == "org/repo"
    assert url_job_id == 999


def test_parse_run_id_url_with_query_only():
    run_id, url_repo, url_job_id = _parse_run_id(
        "https://github.com/org/repo/actions/runs/12345?pr=354"
    )
    assert run_id == 12345
    assert url_repo == "org/repo"
    assert url_job_id is None


def test_parse_run_id_url_with_job_no_query():
    run_id, url_repo, url_job_id = _parse_run_id(
        "https://github.com/org/repo/actions/runs/12345/job/42"
    )
    assert run_id == 12345
    assert url_repo == "org/repo"
    assert url_job_id == 42


def test_parse_run_id_numeric():
    run_id, url_repo, url_job_id = _parse_run_id("12345")
    assert run_id == 12345
    assert url_repo is None
    assert url_job_id is None


def test_job_id_conflict_warning(monkeypatch, capsys):
    captured: list[dict] = []

    def fake_download_run(
        run_id, repo=None, job_id=None, output_dir="./runs", force=False
    ):
        captured.append({"run_id": run_id, "repo": repo, "job_id": job_id})

    monkeypatch.setattr(cli_mod, "download_run", fake_download_run)
    monkeypatch.setattr(
        "sys.argv",
        [
            "gha-downloader",
            "run",
            "download",
            "https://github.com/org/repo/actions/runs/12345/job/111",
            "--job-id",
            "999",
        ],
    )
    cli_mod.main()
    assert captured[0]["job_id"] == 999
    err = capsys.readouterr().err
    assert "--job-id 999 overrides job ID 111 from URL" in err


def test_job_id_no_warning_when_matching(monkeypatch, capsys):
    captured: list[dict] = []

    def fake_download_run(
        run_id, repo=None, job_id=None, output_dir="./runs", force=False
    ):
        captured.append({"run_id": run_id, "repo": repo, "job_id": job_id})

    monkeypatch.setattr(cli_mod, "download_run", fake_download_run)
    monkeypatch.setattr(
        "sys.argv",
        [
            "gha-downloader",
            "run",
            "download",
            "https://github.com/org/repo/actions/runs/12345/job/111",
            "--job-id",
            "111",
        ],
    )
    cli_mod.main()
    assert captured[0]["job_id"] == 111
    err = capsys.readouterr().err
    assert "overrides" not in err
