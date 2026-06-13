from unittest import mock

import pytest

from gha_downloader.downloader import (
    DownloaderError,
    _split_log_by_steps,
    download_run,
)
from gha_downloader.gh import (
    ArtifactData,
    GhNotFoundError,
    JobData,
    RunViewData,
    StepData,
)


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
        "gha_downloader.downloader.get_job_steps",
        mock.Mock(return_value=[]),
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
    assert len(step_files) >= 1


def test_download_run_not_found(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "gha_downloader.downloader.get_run_view",
        mock.Mock(side_effect=GhNotFoundError("not found")),
    )

    with pytest.raises(GhNotFoundError):
        download_run(99999, repo="myorg/myrepo", output_dir=str(tmp_path))


def test_download_run_dir_exists_no_force(monkeypatch, tmp_path):
    run_dir = tmp_path / "12345"
    run_dir.mkdir(parents=True)
    (run_dir / "existing.txt").touch()

    with pytest.raises(DownloaderError):
        download_run(12345, repo="myorg/myrepo", output_dir=str(tmp_path), force=False)
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
        "gha_downloader.downloader.get_job_steps",
        mock.Mock(return_value=[]),
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
        "gha_downloader.downloader.get_job_steps",
        mock.Mock(return_value=[]),
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

    with pytest.raises(DownloaderError):
        download_run(12345, repo="myorg/myrepo", output_dir=str(tmp_path), job_id=99)


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

    mock_artifact = ArtifactData.model_validate(
        {
            "id": 100,
            "name": "my-artifact",
            "size_in_bytes": 100,
            "expired": False,
        }
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
        "gha_downloader.downloader.get_job_steps",
        mock.Mock(return_value=[]),
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


class TestSplitLogBySteps:
    def _make_step(self, number, name, started_at, completed_at=None):
        return StepData(
            name=name,
            status="completed" if completed_at else "in_progress",
            conclusion="success" if completed_at else None,
            number=number,
            startedAt=started_at,
            completedAt=completed_at,
        )

    def test_normal_multi_step_log(self):
        steps = [
            self._make_step(
                1, "Checkout", "2024-01-15T12:00:00Z", "2024-01-15T12:00:10Z"
            ),
            self._make_step(2, "Build", "2024-01-15T12:00:10Z", "2024-01-15T12:01:00Z"),
            self._make_step(3, "Test", "2024-01-15T12:01:00Z", "2024-01-15T12:02:00Z"),
        ]
        log = (
            "2024-01-15T12:00:00.0000000Z checkout line\n"
            "2024-01-15T12:00:05.0000000Z another checkout line\n"
            "2024-01-15T12:00:10.0000000Z build line\n"
            "2024-01-15T12:01:00.0000000Z test line\n"
        )
        result = _split_log_by_steps(log, steps)
        assert "01_checkout" in result
        assert "checkout line" in result["01_checkout"]
        assert "02_build" in result
        assert "build line" in result["02_build"]
        assert "03_test" in result
        assert "test line" in result["03_test"]

    def test_line_at_exact_step_start_boundary(self):
        steps = [
            self._make_step(1, "Setup", "2024-01-15T12:00:00Z", "2024-01-15T12:00:30Z"),
            self._make_step(2, "Run", "2024-01-15T12:00:30Z", "2024-01-15T12:01:00Z"),
        ]
        log = "2024-01-15T12:00:30.0000000Z boundary line\n"
        result = _split_log_by_steps(log, steps)
        assert "boundary line" in result["02_run"]

    def test_final_step_with_completed_at_none(self):
        steps = [
            self._make_step(
                1, "Checkout", "2024-01-15T12:00:00Z", "2024-01-15T12:00:10Z"
            ),
            self._make_step(2, "Build", "2024-01-15T12:00:10Z", None),
        ]
        log = (
            "2024-01-15T12:00:00.0000000Z checkout\n"
            "2024-01-15T12:00:10.0000000Z build line 1\n"
            "2024-01-15T12:05:00.0000000Z build line 2\n"
        )
        result = _split_log_by_steps(log, steps)
        assert "build line 1" in result["02_build"]
        assert "build line 2" in result["02_build"]

    def test_sub_second_timestamp_seven_fractional_digits(self):
        steps = [
            self._make_step(1, "Run", "2024-01-15T12:34:56Z", "2024-01-15T12:35:00Z"),
        ]
        log = "2024-01-15T12:34:56.7654321Z sub-second line\n"
        result = _split_log_by_steps(log, steps)
        assert "sub-second line" in result["01_run"]

    def test_no_matching_steps_all_lines_default(self):
        steps = [
            self._make_step(
                1, "Checkout", "2024-01-15T12:00:00Z", "2024-01-15T12:00:10Z"
            ),
        ]
        log = "2024-01-15T11:59:59.0000000Z early line\n"
        result = _split_log_by_steps(log, steps)
        assert "early line" in result["01_checkout"]
