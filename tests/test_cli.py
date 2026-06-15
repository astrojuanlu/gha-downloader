from unittest import mock

import pytest

import gha_downloader.cli as cli_mod
from gha_downloader.cli import (
    _parse_run_id,
    build_download_parser,
    build_downloader_parser,
)
from gha_downloader.downloader import DownloaderError


def test_url_repo_inference(monkeypatch):
    captured_repo: list[str | None] = []

    def fake_download_job(run_id, job_id, repo=None, output_dir=None, force=False):
        captured_repo.append(repo)

    monkeypatch.setattr(cli_mod, "download_job", fake_download_job)
    monkeypatch.setattr(
        "sys.argv",
        [
            "gha-download",
            "https://github.com/myorg/myrepo/actions/runs/12345",
            "99",
        ],
    )
    cli_mod.main_download()
    assert captured_repo[0] == "myorg/myrepo"


def test_url_repo_explicit_overrides(monkeypatch):
    captured_repo: list[str | None] = []

    def fake_download_job(run_id, job_id, repo=None, output_dir=None, force=False):
        captured_repo.append(repo)

    monkeypatch.setattr(cli_mod, "download_job", fake_download_job)
    monkeypatch.setattr(
        "sys.argv",
        [
            "gha-download",
            "https://github.com/other/repo/actions/runs/12345",
            "99",
            "--repo",
            "explicit/repo",
        ],
    )
    cli_mod.main_download()
    assert captured_repo[0] == "explicit/repo"


def test_parse_run_id_url_with_query():
    run_id, url_repo = _parse_run_id(
        "https://github.com/org/repo/actions/runs/12345?pr=354"
    )
    assert run_id == 12345
    assert url_repo == "org/repo"


def test_parse_run_id_numeric():
    run_id, url_repo = _parse_run_id("12345")
    assert run_id == 12345
    assert url_repo is None


def test_main_download_reaches_service(monkeypatch):
    captured: list[dict] = []

    def fake_download_job(run_id, job_id, repo=None, output_dir=None, force=False):
        captured.append({"run_id": run_id, "job_id": job_id, "repo": repo})

    monkeypatch.setattr(cli_mod, "download_job", fake_download_job)
    monkeypatch.setattr(
        "sys.argv",
        ["gha-download", "12345", "42", "--repo", "myorg/myrepo"],
    )
    cli_mod.main_download()
    assert captured[0]["run_id"] == 12345
    assert captured[0]["job_id"] == 42
    assert captured[0]["repo"] == "myorg/myrepo"


def test_flat_parser_minimal():
    parser = build_download_parser()
    args = parser.parse_args(["12345", "42"])
    assert args.run_id == "12345"
    assert args.job_id == 42
    assert args.repo is None
    assert args.dir == "./runs"
    assert args.verbose == 0


def test_flat_parser_missing_run_id():
    parser = build_download_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_flat_parser_missing_job_id():
    parser = build_download_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["12345"])


def test_flat_parser_invalid_repo():
    parser = build_download_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["12345", "42", "--repo", "invalid"])


def test_flat_parser_verbose_before_run_id():
    parser = build_download_parser()
    args = parser.parse_args(["-vv", "12345", "42"])
    assert args.verbose == 2
    assert args.run_id == "12345"
    assert args.job_id == 42


def test_flat_parser_verbose_after_job_id():
    parser = build_download_parser()
    args = parser.parse_args(["12345", "42", "-vv"])
    assert args.verbose == 2
    assert args.run_id == "12345"
    assert args.job_id == 42


def test_force_flag_rejected():
    parser = build_download_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["12345", "42", "--force"])


def test_list_artifacts_flag_rejected():
    parser = build_download_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["12345", "42", "--list-artifacts"])


def test_artifact_flag_rejected():
    parser = build_download_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["12345", "42", "--artifact", "name"])


class TestDownloaderSubcommands:
    def test_run_show(self, monkeypatch, capsys):
        monkeypatch.setattr(
            cli_mod,
            "get_run_info",
            mock.Mock(return_value={"databaseId": 123, "jobs": []}),
        )
        monkeypatch.setattr(
            "sys.argv",
            ["gha-downloader", "run", "show", "123"],
        )
        cli_mod.main_downloader()
        out = capsys.readouterr().out
        assert "123" in out

    def test_run_show_error(self, monkeypatch):
        monkeypatch.setattr(
            cli_mod,
            "get_run_info",
            mock.Mock(side_effect=DownloaderError("not found")),
        )
        monkeypatch.setattr(
            "sys.argv",
            ["gha-downloader", "run", "show", "99999"],
        )
        with pytest.raises(SystemExit) as exc_info:
            cli_mod.main_downloader()
        assert exc_info.value.code == 2

    def test_run_download(self, monkeypatch, capsys, tmp_path):
        monkeypatch.setattr(
            cli_mod,
            "download_all_jobs_from_run",
            mock.Mock(return_value=tmp_path / "123"),
        )
        monkeypatch.setattr(
            "sys.argv",
            ["gha-downloader", "run", "download", "123"],
        )
        cli_mod.main_downloader()
        out = capsys.readouterr().out
        assert "123" in out

    def test_job_download(self, monkeypatch, capsys, tmp_path):
        monkeypatch.setattr(
            cli_mod,
            "download_job",
            mock.Mock(return_value=tmp_path / "123"),
        )
        monkeypatch.setattr(
            "sys.argv",
            ["gha-downloader", "job", "download", "123", "42"],
        )
        cli_mod.main_downloader()
        out = capsys.readouterr().out
        assert "123" in out

    def test_artifact_list(self, monkeypatch, capsys):
        monkeypatch.setattr(
            cli_mod,
            "list_artifacts",
            mock.Mock(
                return_value=[
                    {
                        "name": "test-results",
                        "size_in_bytes": 2048,
                        "expired": False,
                        "artifact_slug": "test-results",
                    }
                ]
            ),
        )
        monkeypatch.setattr(
            "sys.argv",
            ["gha-downloader", "artifact", "list", "123"],
        )
        cli_mod.main_downloader()
        out = capsys.readouterr().out
        assert "test-results" in out
        assert "slug: test-results" in out

    def test_artifact_list_all(self, monkeypatch, capsys):
        monkeypatch.setattr(
            cli_mod,
            "list_artifacts",
            mock.Mock(
                return_value=[
                    {
                        "name": "old",
                        "size_in_bytes": 512,
                        "expired": True,
                        "artifact_slug": "old",
                    }
                ]
            ),
        )
        monkeypatch.setattr(
            "sys.argv",
            ["gha-downloader", "artifact", "list", "123", "--all"],
        )
        cli_mod.main_downloader()
        out = capsys.readouterr().out
        assert "old" in out
        assert "expired" in out

    def test_artifact_download(self, monkeypatch, capsys, tmp_path):
        monkeypatch.setattr(
            cli_mod,
            "download_artifact",
            mock.Mock(return_value=tmp_path / "123" / "artifacts" / "my-art"),
        )
        monkeypatch.setattr(
            "sys.argv",
            ["gha-downloader", "artifact", "download", "123", "My Art"],
        )
        cli_mod.main_downloader()
        out = capsys.readouterr().out
        assert "my-art" in out

    def test_artifact_download_error(self, monkeypatch):
        monkeypatch.setattr(
            cli_mod,
            "download_artifact",
            mock.Mock(side_effect=DownloaderError("not found")),
        )
        monkeypatch.setattr(
            "sys.argv",
            ["gha-downloader", "artifact", "download", "123", "missing"],
        )
        with pytest.raises(SystemExit) as exc_info:
            cli_mod.main_downloader()
        assert exc_info.value.code == 2

    def test_run_download_error(self, monkeypatch):
        monkeypatch.setattr(
            cli_mod,
            "download_all_jobs_from_run",
            mock.Mock(side_effect=DownloaderError("already exists")),
        )
        monkeypatch.setattr(
            "sys.argv",
            ["gha-downloader", "run", "download", "123"],
        )
        with pytest.raises(SystemExit) as exc_info:
            cli_mod.main_downloader()
        assert exc_info.value.code == 2

    def test_job_download_error(self, monkeypatch):
        monkeypatch.setattr(
            cli_mod,
            "download_job",
            mock.Mock(side_effect=DownloaderError("not found")),
        )
        monkeypatch.setattr(
            "sys.argv",
            ["gha-downloader", "job", "download", "123", "99999"],
        )
        with pytest.raises(SystemExit) as exc_info:
            cli_mod.main_downloader()
        assert exc_info.value.code == 2


class TestDownloaderParser:
    def test_run_show_parser(self):
        parser = build_downloader_parser()
        args = parser.parse_args(["run", "show", "123"])
        assert args.command == "run"
        assert args.run_command == "show"
        assert args.run_id == 123

    def test_run_download_parser(self):
        parser = build_downloader_parser()
        args = parser.parse_args(["run", "download", "123", "--force"])
        assert args.run_command == "download"
        assert args.force is True

    def test_job_download_parser(self):
        parser = build_downloader_parser()
        args = parser.parse_args(["job", "download", "123", "42"])
        assert args.command == "job"
        assert args.job_id == 42

    def test_artifact_list_parser(self):
        parser = build_downloader_parser()
        args = parser.parse_args(["artifact", "list", "123", "--all"])
        assert args.artifact_command == "list"
        assert args.show_all is True

    def test_artifact_download_parser(self):
        parser = build_downloader_parser()
        args = parser.parse_args(["artifact", "download", "123", "my-artifact"])
        assert args.artifact_command == "download"
        assert args.artifact_name == "my-artifact"
