from unittest import mock

import pytest

import gha_downloader.cli as cli_mod
from gha_downloader.cli import _parse_run_id, build_download_parser
from gha_downloader.gh import ArtifactData


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
            "gha-download",
            "https://github.com/myorg/myrepo/actions/runs/12345",
        ],
    )
    cli_mod.main_download()
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
            "gha-download",
            "https://github.com/other/repo/actions/runs/12345",
            "--repo",
            "explicit/repo",
        ],
    )
    cli_mod.main_download()
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
            "gha-download",
            "https://github.com/org/repo/actions/runs/12345/job/111",
            "--job-id",
            "999",
        ],
    )
    cli_mod.main_download()
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
            "gha-download",
            "https://github.com/org/repo/actions/runs/12345/job/111",
            "--job-id",
            "111",
        ],
    )
    cli_mod.main_download()
    assert captured[0]["job_id"] == 111
    err = capsys.readouterr().err
    assert "overrides" not in err


def test_flat_parser_minimal():
    parser = build_download_parser()
    args = parser.parse_args(["12345"])
    assert args.run_id == "12345"
    assert args.repo is None
    assert args.job_id is None
    assert args.dir == "./runs"
    assert args.force is False
    assert args.verbose == 0


def test_flat_parser_all_flags():
    parser = build_download_parser()
    args = parser.parse_args(
        [
            "12345",
            "--repo",
            "myorg/myrepo",
            "--job-id",
            "42",
            "--dir",
            "/tmp/out",
            "--force",
            "-vv",
        ]
    )
    assert args.run_id == "12345"
    assert args.repo == "myorg/myrepo"
    assert args.job_id == 42
    assert args.dir == "/tmp/out"
    assert args.force is True
    assert args.verbose == 2


def test_flat_parser_missing_run_id():
    parser = build_download_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_flat_parser_invalid_repo():
    parser = build_download_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["12345", "--repo", "invalid"])


def test_flat_parser_verbose_before_run_id():
    parser = build_download_parser()
    args = parser.parse_args(["-vv", "12345"])
    assert args.verbose == 2
    assert args.run_id == "12345"


def test_flat_parser_verbose_after_run_id():
    parser = build_download_parser()
    args = parser.parse_args(["12345", "-vv"])
    assert args.verbose == 2
    assert args.run_id == "12345"


def test_main_download_reaches_download_run(monkeypatch):
    captured: list[dict] = []

    def fake_download_run(
        run_id, repo=None, job_id=None, output_dir="./runs", force=False
    ):
        captured.append({"run_id": run_id, "repo": repo, "job_id": job_id})

    monkeypatch.setattr(cli_mod, "download_run", fake_download_run)
    monkeypatch.setattr(
        "sys.argv",
        ["gha-download", "12345", "--repo", "myorg/myrepo"],
    )
    cli_mod.main_download()
    assert captured[0]["run_id"] == 12345
    assert captured[0]["repo"] == "myorg/myrepo"


class TestListArtifactsJobFilter:
    def test_job_id_filters_artifacts(self, monkeypatch, capsys):
        art1 = ArtifactData.model_validate(
            {"id": 100, "name": "test-results", "size_in_bytes": 2048, "expired": False}
        )
        art2 = ArtifactData.model_validate(
            {"id": 200, "name": "build-logs", "size_in_bytes": 1024, "expired": False}
        )
        monkeypatch.setattr(
            "gha_downloader.cli.get_artifacts",
            mock.Mock(return_value=[art1, art2]),
        )
        monkeypatch.setattr(
            "gha_downloader.cli.get_log_text",
            mock.Mock(return_value="Artifact ID is 100\n"),
        )
        monkeypatch.setattr(
            "sys.argv",
            ["gha-download", "12345", "--list-artifacts", "--job-id", "42"],
        )
        with pytest.raises(SystemExit) as exc_info:
            cli_mod.main_download()
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "test-results" in out
        assert "build-logs" not in out

    def test_job_id_no_artifact_ids_empty_output(self, monkeypatch, capsys):
        art1 = ArtifactData.model_validate(
            {"id": 100, "name": "test-results", "size_in_bytes": 2048, "expired": False}
        )
        monkeypatch.setattr(
            "gha_downloader.cli.get_artifacts",
            mock.Mock(return_value=[art1]),
        )
        monkeypatch.setattr(
            "gha_downloader.cli.get_log_text",
            mock.Mock(return_value="no artifact lines here\n"),
        )
        monkeypatch.setattr(
            "sys.argv",
            ["gha-download", "12345", "--list-artifacts", "--job-id", "42"],
        )
        with pytest.raises(SystemExit) as exc_info:
            cli_mod.main_download()
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert out == ""

    def test_no_job_id_returns_full_list(self, monkeypatch, capsys):
        art1 = ArtifactData.model_validate(
            {"id": 100, "name": "test-results", "size_in_bytes": 2048, "expired": False}
        )
        art2 = ArtifactData.model_validate(
            {"id": 200, "name": "build-logs", "size_in_bytes": 1024, "expired": False}
        )
        monkeypatch.setattr(
            "gha_downloader.cli.get_artifacts",
            mock.Mock(return_value=[art1, art2]),
        )
        monkeypatch.setattr(
            "sys.argv",
            ["gha-download", "12345", "--list-artifacts"],
        )
        with pytest.raises(SystemExit) as exc_info:
            cli_mod.main_download()
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "test-results" in out
        assert "build-logs" in out
