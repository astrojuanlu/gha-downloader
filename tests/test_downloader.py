from unittest import mock

import pytest

from gha_downloader.downloader import (
    download_run,
    split_log,
)
from gha_downloader.gh import (
    ArtifactData,
    GhNotFoundError,
    JobData,
    RunViewData,
    StepData,
)

...


def test_split_log_with_groups():
    log = (
        "##[group]Run tests\nline 1\nline 2\n"
        "##[group]Run build\nbuild line\n##[endgroup]\n"
    )
    result = split_log(log)
    assert len(result) == 2
    assert result[0][0] == "tests"
    assert result[0][1] == "line 1\nline 2\n"
    assert result[1][0] == "build"
    assert result[1][1] == "build line\n##[endgroup]\n"


def test_split_log_empty_group_name():
    log = "##[group]\ncontent\n"
    result = split_log(log)
    assert result[0][0] == ""


def test_split_log_pre_content():
    log = "pre content\n##[group]Step 1\nstep content\n"
    result = split_log(log)
    assert result[0][0] == "pre"
    assert result[0][1] == "pre content\n"
    assert result[1][0] == "Step 1"
    assert result[1][1] == "step content\n"


def test_split_log_with_timestamps():
    log = (
        "2024-01-01T00:00:00Z setup\n"
        "2024-01-01T00:00:01Z ##[group]Run tests\n"
        "2024-01-01T00:00:02Z test output\n"
    )
    result = split_log(log)
    assert result[0][0] == "pre"
    assert "setup" in result[0][1]
    assert result[1][0] == "tests"
    assert "test output" in result[1][1]


def test_download_run_fetches_metadata_and_saves(monkeypatch, tmp_path):
    mock_run_view = RunViewData(
        databaseId=12345,
        name="CI",
        status="completed",
        conclusion="success",
        createdAt="2024-01-01T00:00:00Z",
        displayTitle="Fix bug",
        event="push",
        headBranch="main",
        headSha="abc123",
        url="https://github.com/org/repo/actions/runs/12345",
        workflowName="CI",
        jobs=[
            JobData(
                databaseId=42,
                name="test-job",
                status="completed",
                conclusion="success",
                startedAt="2024-01-01T00:00:00Z",
                completedAt="2024-01-01T00:01:00Z",
                steps=[
                    StepData(
                        name="Checkout",
                        status="completed",
                        conclusion="success",
                        number=1,
                    ),
                ],
            )
        ],
    )

    monkeypatch.setattr(
        "gha_downloader.downloader.get_run_view",
        mock.Mock(return_value=mock_run_view),
    )
    monkeypatch.setattr(
        "gha_downloader.downloader.get_log_text",
        mock.Mock(return_value="##[group]Run setup\nsetup log\n"),
    )
    monkeypatch.setattr(
        "gha_downloader.downloader.get_artifacts",
        mock.Mock(return_value=[]),
    )

    download_run(12345, repo="myorg/myrepo", output_dir=str(tmp_path))

    run_json = tmp_path / "12345" / "run.json"
    assert run_json.exists()

    logs_dir = tmp_path / "12345" / "logs" / "test-job"
    assert logs_dir.exists()
    step_files = list(logs_dir.glob("*.txt"))
    assert len(step_files) == 1


def test_download_run_not_found(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "gha_downloader.downloader.get_run_view",
        mock.Mock(side_effect=GhNotFoundError("not found")),
    )

    with pytest.raises(SystemExit) as exc:
        download_run(99999, repo="myorg/myrepo", output_dir=str(tmp_path))
    assert exc.value.code == 2


def test_download_run_dir_exists_no_force(monkeypatch, tmp_path):
    run_dir = tmp_path / "12345"
    run_dir.mkdir(parents=True)
    (run_dir / "existing.txt").touch()

    with pytest.raises(SystemExit) as exc:
        download_run(12345, repo="myorg/myrepo", output_dir=str(tmp_path), force=False)
    assert exc.value.code == 2
    assert (run_dir / "existing.txt").exists()


def test_download_run_dir_exists_force(monkeypatch, tmp_path):
    run_dir = tmp_path / "12345"
    run_dir.mkdir(parents=True)
    (run_dir / "existing.txt").touch()

    mock_run_view = RunViewData(
        databaseId=12345,
        name="CI",
        status="completed",
        conclusion="success",
        createdAt="2024-01-01T00:00:00Z",
        displayTitle="Fix bug",
        event="push",
        headBranch="main",
        headSha="abc123",
        url="https://github.com/org/repo/actions/runs/12345",
        workflowName="CI",
    )

    monkeypatch.setattr(
        "gha_downloader.downloader.get_run_view",
        mock.Mock(return_value=mock_run_view),
    )
    monkeypatch.setattr(
        "gha_downloader.downloader.get_log_text",
        mock.Mock(return_value="log"),
    )
    monkeypatch.setattr(
        "gha_downloader.downloader.get_artifacts",
        mock.Mock(return_value=[]),
    )

    download_run(12345, repo="myorg/myrepo", output_dir=str(tmp_path), force=True)
    assert not (run_dir / "existing.txt").exists()
    assert (run_dir / "run.json").exists()


def test_download_run_job_filter(monkeypatch, tmp_path):
    mock_run_view = RunViewData(
        databaseId=12345,
        name="CI",
        status="completed",
        conclusion="success",
        createdAt="2024-01-01T00:00:00Z",
        displayTitle="Fix bug",
        event="push",
        headBranch="main",
        headSha="abc123",
        url="https://github.com/org/repo/actions/runs/12345",
        workflowName="CI",
        jobs=[
            JobData(
                databaseId=1,
                name="job-one",
                status="completed",
                conclusion="success",
                startedAt="2024-01-01T00:00:00Z",
                completedAt="2024-01-01T00:01:00Z",
            ),
            JobData(
                databaseId=2,
                name="job-two",
                status="completed",
                conclusion="success",
                startedAt="2024-01-01T00:00:00Z",
                completedAt="2024-01-01T00:01:00Z",
            ),
        ],
    )

    monkeypatch.setattr(
        "gha_downloader.downloader.get_run_view",
        mock.Mock(return_value=mock_run_view),
    )
    mock_get_log = mock.Mock(return_value="log text")
    monkeypatch.setattr(
        "gha_downloader.downloader.get_log_text",
        mock_get_log,
    )
    monkeypatch.setattr(
        "gha_downloader.downloader.get_artifacts",
        mock.Mock(return_value=[]),
    )

    download_run(12345, repo="myorg/myrepo", output_dir=str(tmp_path), job_id=1)

    assert mock_get_log.call_count == 1

    logs_dir = tmp_path / "12345" / "logs"
    assert (logs_dir / "job-one").exists()
    assert not (logs_dir / "job-two").exists()


def test_download_run_job_filter_not_found(monkeypatch, tmp_path):
    mock_run_view = RunViewData(
        databaseId=12345,
        name="CI",
        status="completed",
        conclusion="success",
        createdAt="2024-01-01T00:00:00Z",
        displayTitle="Fix bug",
        event="push",
        headBranch="main",
        headSha="abc123",
        url="https://github.com/org/repo/actions/runs/12345",
        workflowName="CI",
        jobs=[
            JobData(
                databaseId=1,
                name="job-one",
                status="completed",
                conclusion="success",
                startedAt="2024-01-01T00:00:00Z",
                completedAt="2024-01-01T00:01:00Z",
            ),
        ],
    )

    monkeypatch.setattr(
        "gha_downloader.downloader.get_run_view",
        mock.Mock(return_value=mock_run_view),
    )

    with pytest.raises(SystemExit) as exc:
        download_run(12345, repo="myorg/myrepo", output_dir=str(tmp_path), job_id=99)
    assert exc.value.code == 2


def test_download_run_with_artifacts(monkeypatch, tmp_path):
    mock_run_view = RunViewData(
        databaseId=12345,
        name="CI",
        status="completed",
        conclusion="success",
        createdAt="2024-01-01T00:00:00Z",
        displayTitle="Fix bug",
        event="push",
        headBranch="main",
        headSha="abc123",
        url="https://github.com/org/repo/actions/runs/12345",
        workflowName="CI",
        jobs=[
            JobData(
                databaseId=1,
                name="test-job",
                status="completed",
                conclusion="success",
                startedAt="2024-01-01T00:00:00Z",
                completedAt="2024-01-01T00:01:00Z",
            ),
        ],
    )

    mock_artifact = ArtifactData(
        id=100,
        name="my-artifact",
        size_in_bytes=100,
        expired=False,
    )

    monkeypatch.setattr(
        "gha_downloader.downloader.get_run_view",
        mock.Mock(return_value=mock_run_view),
    )
    monkeypatch.setattr(
        "gha_downloader.downloader.get_log_text",
        mock.Mock(return_value="log"),
    )
    monkeypatch.setattr(
        "gha_downloader.downloader.get_artifacts",
        mock.Mock(return_value=[mock_artifact]),
    )
    mock_dl = mock.Mock()
    monkeypatch.setattr(
        "gha_downloader.downloader.gh_download_artifact",
        mock_dl,
    )

    download_run(12345, repo="myorg/myrepo", output_dir=str(tmp_path))

    assert mock_dl.call_count == 1
    assert mock_dl.call_args[0][1] == "my-artifact"
