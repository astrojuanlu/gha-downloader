import json
import subprocess
import time
from unittest import mock

import pytest

from gha_downloader.gh import (
    GhApiError,
    GhAutoDetectError,
    GhExpiredArtifactError,
    GhNetworkError,
    GhNotFoundError,
    GhNotInstalledError,
    GhSpawnError,
    RunViewData,
    find_gh,
    get_artifacts,
    get_log_text,
    get_run_view,
    run_gh,
)


def _make_result(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        args=["gh", "test"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_find_gh_found(monkeypatch):
    find_gh.cache_clear()
    monkeypatch.setattr(
        "gha_downloader.gh.shutil.which", mock.Mock(return_value="/usr/bin/gh")
    )
    assert find_gh() == "/usr/bin/gh"


def test_find_gh_not_found(monkeypatch):
    find_gh.cache_clear()
    monkeypatch.setattr("gha_downloader.gh.shutil.which", mock.Mock(return_value=None))
    with pytest.raises(GhNotInstalledError):
        find_gh()


def test_run_gh_success(monkeypatch):
    find_gh.cache_clear()
    mock_run = mock.Mock(return_value=_make_result(stdout="ok"))
    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(
        "gha_downloader.gh.shutil.which", mock.Mock(return_value="/usr/bin/gh")
    )
    result = run_gh(["some", "command"])
    assert result.stdout == "ok"
    assert mock_run.call_args[0][0] == ["/usr/bin/gh", "some", "command"]


def test_run_gh_with_repo(monkeypatch):
    find_gh.cache_clear()
    mock_run = mock.Mock(return_value=_make_result(stdout="ok"))
    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(
        "gha_downloader.gh.shutil.which", mock.Mock(return_value="/usr/bin/gh")
    )
    run_gh(["run", "view", "12345", "-R", "myorg/myrepo"])
    called_args = mock_run.call_args[0][0]
    assert "-R" in called_args
    assert "myorg/myrepo" in called_args


def test_run_gh_not_found(monkeypatch):
    find_gh.cache_clear()
    mock_run = mock.Mock(
        return_value=_make_result(returncode=1, stderr="not found (HTTP 404)")
    )
    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(
        "gha_downloader.gh.shutil.which", mock.Mock(return_value="/usr/bin/gh")
    )
    with pytest.raises(GhNotFoundError):
        run_gh(["test"])


def test_run_gh_expired(monkeypatch):
    find_gh.cache_clear()
    mock_run = mock.Mock(
        return_value=_make_result(returncode=1, stderr="artifact expired")
    )
    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(
        "gha_downloader.gh.shutil.which", mock.Mock(return_value="/usr/bin/gh")
    )
    with pytest.raises(GhExpiredArtifactError):
        run_gh(["test"])


def test_run_gh_network_error_retries(monkeypatch):
    find_gh.cache_clear()
    call_count = [0]

    def side_effect(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] < 3:
            return _make_result(returncode=1, stderr="connect: network is unreachable")
        return _make_result(stdout="ok")

    monkeypatch.setattr(subprocess, "run", side_effect)
    monkeypatch.setattr(time, "sleep", mock.Mock())
    monkeypatch.setattr(
        "gha_downloader.gh.shutil.which", mock.Mock(return_value="/usr/bin/gh")
    )
    result = run_gh(["test"])
    assert result.stdout == "ok"
    assert call_count[0] == 3
    assert time.sleep.call_count == 2


def test_run_gh_network_error_exhausted(monkeypatch):
    find_gh.cache_clear()
    mock_run = mock.Mock(
        return_value=_make_result(
            returncode=1, stderr="connect: network is unreachable"
        )
    )
    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(time, "sleep", mock.Mock())
    monkeypatch.setattr(
        "gha_downloader.gh.shutil.which", mock.Mock(return_value="/usr/bin/gh")
    )
    with pytest.raises(GhNetworkError):
        run_gh(["test"])


def test_run_gh_auto_detect_error(monkeypatch):
    find_gh.cache_clear()
    mock_run = mock.Mock(
        return_value=_make_result(returncode=1, stderr="no git remotes")
    )
    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(
        "gha_downloader.gh.shutil.which", mock.Mock(return_value="/usr/bin/gh")
    )
    with pytest.raises(GhAutoDetectError):
        run_gh(["test"])


def test_run_gh_api_error(monkeypatch):
    find_gh.cache_clear()
    mock_run = mock.Mock(
        return_value=_make_result(returncode=1, stderr="some API error")
    )
    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(
        "gha_downloader.gh.shutil.which", mock.Mock(return_value="/usr/bin/gh")
    )
    with pytest.raises(GhApiError):
        run_gh(["test"])


def test_get_run_view(monkeypatch):
    find_gh.cache_clear()
    mock_run = mock.Mock(
        return_value=_make_result(
            stdout=json.dumps(
                {
                    "databaseId": 12345,
                    "name": "CI",
                    "status": "completed",
                    "conclusion": "success",
                    "createdAt": "2024-01-01T00:00:00Z",
                    "displayTitle": "Fix bug",
                    "event": "push",
                    "headBranch": "main",
                    "headSha": "abc123",
                    "url": "https://github.com/org/repo/actions/runs/12345",
                    "workflowName": "CI",
                    "jobs": [],
                }
            )
        )
    )
    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(
        "gha_downloader.gh.shutil.which", mock.Mock(return_value="/usr/bin/gh")
    )
    result = get_run_view("12345")
    assert isinstance(result, RunViewData)
    assert result.databaseId == 12345
    assert result.status == "completed"
    assert result.jobs == []


def test_get_run_view_with_repo(monkeypatch):
    find_gh.cache_clear()
    mock_run = mock.Mock(
        return_value=_make_result(
            stdout=json.dumps(
                {
                    "databaseId": 12345,
                    "name": "CI",
                    "status": "completed",
                    "conclusion": "success",
                    "createdAt": "2024-01-01T00:00:00Z",
                    "displayTitle": "Fix bug",
                    "event": "push",
                    "headBranch": "main",
                    "headSha": "abc123",
                    "url": "https://github.com/org/repo/actions/runs/12345",
                    "workflowName": "CI",
                    "jobs": [],
                }
            )
        )
    )
    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(
        "gha_downloader.gh.shutil.which", mock.Mock(return_value="/usr/bin/gh")
    )
    get_run_view("12345", repo="myorg/myrepo")
    called_args = mock_run.call_args[0][0]
    assert "-R" in called_args
    assert "myorg/myrepo" in called_args


def test_get_log_text(monkeypatch):
    find_gh.cache_clear()
    mock_run = mock.Mock(
        return_value=_make_result(stdout="##[group]Run tests\nlog line\n##[endgroup]")
    )
    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(
        "gha_downloader.gh.shutil.which", mock.Mock(return_value="/usr/bin/gh")
    )
    result = get_log_text("myorg/myrepo", 42)
    assert "##[group]" in result


def test_get_log_text_with_repo(monkeypatch):
    find_gh.cache_clear()
    mock_run = mock.Mock(return_value=_make_result(stdout="log text"))
    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(
        "gha_downloader.gh.shutil.which", mock.Mock(return_value="/usr/bin/gh")
    )
    get_log_text("myorg/myrepo", 42)
    called_args = mock_run.call_args[0][0]
    endpoint = called_args[-1]
    assert "myorg/myrepo" in endpoint


def test_get_artifacts(monkeypatch):
    find_gh.cache_clear()
    mock_run = mock.Mock(
        return_value=_make_result(
            stdout=json.dumps(
                {
                    "id": 1,
                    "name": "build-output",
                    "size_in_bytes": 1024,
                    "expired": False,
                    "archive_download_url": "https://api.github.com/...",
                }
            )
            + "\n"
            + json.dumps(
                {
                    "id": 2,
                    "name": "build-output-2",
                    "size_in_bytes": 2048,
                    "expired": False,
                    "archive_download_url": "https://api.github.com/...",
                }
            )
        )
    )
    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(
        "gha_downloader.gh.shutil.which",
        mock.Mock(return_value="/usr/bin/gh"),
    )
    result = get_artifacts("12345", repo="myorg/myrepo")
    assert len(result) == 2
    assert result[0].name == "build-output"
    assert result[0].expired is False


def test_get_artifacts_expired(monkeypatch):
    find_gh.cache_clear()
    mock_run = mock.Mock(
        return_value=_make_result(
            stdout=json.dumps(
                {
                    "id": 1,
                    "name": "expired-artifact",
                    "size_in_bytes": 0,
                    "expired": True,
                    "archive_download_url": None,
                }
            )
        )
    )
    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(
        "gha_downloader.gh.shutil.which",
        mock.Mock(return_value="/usr/bin/gh"),
    )
    result = get_artifacts("12345", repo="myorg/myrepo")
    assert len(result) == 1
    assert result[0].expired is True


def test_run_gh_oserror_retries(monkeypatch):
    find_gh.cache_clear()
    call_count = [0]

    def side_effect(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] < 3:
            raise OSError("spawn failed")
        return _make_result(stdout="ok")

    monkeypatch.setattr(subprocess, "run", side_effect)
    monkeypatch.setattr(time, "sleep", mock.Mock())
    monkeypatch.setattr(
        "gha_downloader.gh.shutil.which",
        mock.Mock(return_value="/usr/bin/gh"),
    )
    result = run_gh(["test"])
    assert result.stdout == "ok"
    assert call_count[0] == 3


def test_run_gh_oserror_exhausted(monkeypatch):
    find_gh.cache_clear()
    monkeypatch.setattr(
        subprocess, "run", mock.Mock(side_effect=OSError("spawn failed"))
    )
    mock_sleep = mock.Mock()
    monkeypatch.setattr(time, "sleep", mock_sleep)
    monkeypatch.setattr(
        "gha_downloader.gh.shutil.which",
        mock.Mock(return_value="/usr/bin/gh"),
    )
    with pytest.raises(GhSpawnError):
        run_gh(["test"])
