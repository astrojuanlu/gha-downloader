from contextlib import contextmanager
from unittest import mock

import pytest

from gha_downloader.downloader import (
    DownloaderError,
    _extract_artifact_ids,
    _split_log_by_groups,
    download_run,
)
from gha_downloader.gh import (
    GhAutoDetectError,
    GhNotFoundError,
    JobData,
    ReferencedWorkflow,
    RunViewData,
    RunWorkflowInfo,
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
    mock_wf_info = RunWorkflowInfo(
        path=".github/workflows/ci.yml",
        head_sha="abc123",
        referenced_workflows=[],
    )

    monkeypatch.setattr(
        "gha_downloader.downloader.get_run_view",
        mock.Mock(return_value=mock_run_view),
    )
    monkeypatch.setattr(
        "gha_downloader.downloader.get_run_workflow_info",
        mock.Mock(return_value=mock_wf_info),
    )
    monkeypatch.setattr(
        "gha_downloader.downloader.get_workflow_yaml_content",
        mock.Mock(return_value=None),
    )
    monkeypatch.setattr(
        "gha_downloader.downloader.get_log_text",
        mock.Mock(return_value="##[group]Run setup\nsetup log\n"),
    )
    monkeypatch.setattr(
        "gha_downloader.downloader.get_job_steps",
        mock.Mock(return_value=[]),
    )

    download_run(12345, repo="myorg/myrepo", output_dir=str(tmp_path))

    run_json = tmp_path / "12345" / "run.json"
    assert run_json.exists()

    logs_dir = tmp_path / "12345" / "logs" / "test-job"
    assert logs_dir.exists()
    assert (logs_dir / "full.log").exists()


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
    mock_wf_info = RunWorkflowInfo(
        path=".github/workflows/ci.yml",
        head_sha="abc123",
        referenced_workflows=[],
    )

    monkeypatch.setattr(
        "gha_downloader.downloader.get_run_view",
        mock.Mock(return_value=mock_run_view),
    )
    monkeypatch.setattr(
        "gha_downloader.downloader.get_run_workflow_info",
        mock.Mock(return_value=mock_wf_info),
    )
    monkeypatch.setattr(
        "gha_downloader.downloader.get_workflow_yaml_content",
        mock.Mock(return_value=None),
    )
    monkeypatch.setattr(
        "gha_downloader.downloader.get_log_text",
        mock.Mock(return_value="log"),
    )
    monkeypatch.setattr(
        "gha_downloader.downloader.get_job_steps",
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
    mock_wf_info = RunWorkflowInfo(
        path=".github/workflows/ci.yml",
        head_sha="abc123",
        referenced_workflows=[],
    )

    monkeypatch.setattr(
        "gha_downloader.downloader.get_run_view",
        mock.Mock(return_value=mock_run_view),
    )
    monkeypatch.setattr(
        "gha_downloader.downloader.get_run_workflow_info",
        mock.Mock(return_value=mock_wf_info),
    )
    monkeypatch.setattr(
        "gha_downloader.downloader.get_workflow_yaml_content",
        mock.Mock(return_value=None),
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


def test_download_run_does_not_download_artifacts(monkeypatch, tmp_path):
    """download_run no longer downloads artifacts; no artifacts/ dir created."""
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
    mock_wf_info = RunWorkflowInfo(
        path=".github/workflows/ci.yml",
        head_sha="abc123",
        referenced_workflows=[],
    )

    monkeypatch.setattr(
        "gha_downloader.downloader.get_run_view",
        mock.Mock(return_value=mock_run_view),
    )
    monkeypatch.setattr(
        "gha_downloader.downloader.get_run_workflow_info",
        mock.Mock(return_value=mock_wf_info),
    )
    monkeypatch.setattr(
        "gha_downloader.downloader.get_workflow_yaml_content",
        mock.Mock(return_value=None),
    )
    monkeypatch.setattr(
        "gha_downloader.downloader.get_log_text",
        mock.Mock(return_value="log"),
    )
    monkeypatch.setattr(
        "gha_downloader.downloader.get_job_steps",
        mock.Mock(return_value=[]),
    )

    download_run(12345, repo="myorg/myrepo", output_dir=str(tmp_path))

    assert not (tmp_path / "12345" / "artifacts").exists()


def test_download_run_reusable_workflow_creates_per_step_files(monkeypatch, tmp_path):
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
    mock_wf_info = RunWorkflowInfo(
        path=".github/workflows/ci.yml",
        head_sha="abc123",
        referenced_workflows=[
            ReferencedWorkflow(path=".github/workflows/reusable.yml", sha="def456"),
        ],
    )

    mock_steps = [
        StepData(name="Checkout", status="completed", conclusion="success", number=1),
        StepData(name="Build", status="completed", conclusion="success", number=2),
    ]

    monkeypatch.setattr(
        "gha_downloader.downloader.get_run_view",
        mock.Mock(return_value=mock_run_view),
    )
    monkeypatch.setattr(
        "gha_downloader.downloader.get_run_workflow_info",
        mock.Mock(return_value=mock_wf_info),
    )
    monkeypatch.setattr(
        "gha_downloader.downloader.get_log_text",
        mock.Mock(
            return_value=(
                "##[group]Run checkout\ncheckout output\n"
                "##[group]Run make\nbuild output\n"
            )
        ),
    )
    monkeypatch.setattr(
        "gha_downloader.downloader.get_job_steps",
        mock.Mock(return_value=mock_steps),
    )

    download_run(12345, repo="myorg/myrepo", output_dir=str(tmp_path))

    job_logs_dir = tmp_path / "12345" / "logs" / "test-job"
    assert (job_logs_dir / "full.log").exists()
    step_files = list(job_logs_dir.glob("*.txt"))
    assert len(step_files) >= 1


class TestSplitLogByGroups:
    def _make_step(self, number, name, conclusion="success"):
        return StepData(
            name=name,
            status="completed",
            conclusion=conclusion,
            number=number,
        )

    def test_normal_multi_step_log(self):
        steps = [
            self._make_step(1, "Set up Job"),
            self._make_step(2, "Checkout"),
            self._make_step(3, "Build"),
            self._make_step(4, "Test"),
        ]
        log = (
            "2024-01-15T12:00:00Z ##[group]Set up job\n"
            "setup output\n"
            "2024-01-15T12:00:01Z ##[group]Run checkout\n"
            "checkout output\n"
            "2024-01-15T12:00:10Z ##[group]Run build\n"
            "build output\n"
            "2024-01-15T12:01:00Z ##[group]Run test\n"
            "test output\n"
        )
        result = _split_log_by_groups(log, steps)
        assert "setup output" in result["01_set-up-job"]
        assert "checkout output" in result["02_checkout"]
        assert "build output" in result["03_build"]
        assert "test output" in result["04_test"]

    def test_lines_before_first_marker_go_to_first_step(self):
        steps = [
            self._make_step(1, "Set up Job"),
            self._make_step(2, "Checkout"),
            self._make_step(3, "Build"),
        ]
        log = (
            "2024-01-15T12:00:00Z setup line\n"
            "2024-01-15T12:00:01Z another setup line\n"
            "2024-01-15T12:00:02Z ##[group]Run checkout\n"
            "checkout output\n"
            "2024-01-15T12:00:10Z ##[group]Run build\n"
            "build output\n"
        )
        result = _split_log_by_groups(log, steps)
        assert "setup line" in result["01_set-up-job"]
        assert "another setup line" in result["01_set-up-job"]
        assert "checkout output" in result["02_checkout"]
        assert "build output" in result["03_build"]

    def test_skipped_step_excluded_from_index(self):
        steps = [
            self._make_step(1, "Set up Job"),
            self._make_step(2, "Checkout"),
            self._make_step(3, "Lint", conclusion="skipped"),
            self._make_step(4, "Build"),
        ]
        log = (
            "2024-01-15T12:00:00Z ##[group]Set up job\n"
            "setup output\n"
            "2024-01-15T12:00:10Z ##[group]Run checkout\n"
            "checkout output\n"
            "2024-01-15T12:00:20Z ##[group]Run build\n"
            "build output\n"
        )
        result = _split_log_by_groups(log, steps)
        assert "03_lint" not in result
        assert "setup output" in result["01_set-up-job"]
        assert "checkout output" in result["02_checkout"]
        assert "build output" in result["04_build"]

    def test_extra_markers_bucketed_to_last_step(self):
        steps = [
            self._make_step(1, "Set up Job"),
            self._make_step(2, "Checkout"),
        ]
        log = (
            "2024-01-15T12:00:00Z ##[group]Set up job\n"
            "setup output\n"
            "2024-01-15T12:00:10Z ##[group]Run checkout\n"
            "checkout output\n"
            "2024-01-15T12:00:20Z ##[group]Run extra\n"
            "extra output\n"
        )
        result = _split_log_by_groups(log, steps)
        assert "extra output" in result["02_checkout"]

    def test_runner_prefix_does_not_advance_index(self):
        steps = [
            self._make_step(1, "Set up Job"),
            self._make_step(2, "Checkout"),
            self._make_step(3, "Build"),
        ]
        log = (
            "2024-01-15T12:00:00Z ##[group]Runner Image Provisioner\n"
            "runner info\n"
            "2024-01-15T12:00:05Z ##[group]Run checkout\n"
            "checkout output\n"
            "2024-01-15T12:00:10Z ##[group]Run build\n"
            "build output\n"
        )
        result = _split_log_by_groups(log, steps)
        assert "runner info" in result["01_set-up-job"]
        assert "checkout output" in result["02_checkout"]
        assert "build output" in result["03_build"]

    def test_yaml_names_override_step_names(self):
        steps = [
            self._make_step(1, "Set up Job"),
            self._make_step(2, "Run tests"),
            self._make_step(3, "Build"),
        ]
        log = (
            "2024-01-15T12:00:00Z ##[group]Set up job\n"
            "setup output\n"
            "2024-01-15T12:00:01Z ##[group]Run pytest\n"
            "test output\n"
            "2024-01-15T12:00:10Z ##[group]Run make\n"
            "build output\n"
        )
        yaml_names = {2: "Integration Tests", 3: "Compile"}
        result = _split_log_by_groups(log, steps, yaml_names)
        assert "01_set-up-job" in result
        assert "02_integration-tests" in result


def test_download_run_removes_dir_on_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "gha_downloader.downloader.get_run_view",
        mock.Mock(side_effect=GhAutoDetectError("Cannot auto-detect repository.")),
    )

    with pytest.raises(GhAutoDetectError):
        download_run(12345, repo=None, output_dir=str(tmp_path))

    run_dir = tmp_path / "12345"
    assert not run_dir.exists()


def test_download_run_job_filter_bar_total_is_one(monkeypatch, tmp_path):
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
    mock_wf_info = RunWorkflowInfo(
        path=".github/workflows/ci.yml",
        head_sha="abc123",
        referenced_workflows=[],
    )

    captured_totals: list[int] = []

    @contextmanager
    def _capturing_alive_bar(total, *, title="", file=None, ctrl_c=False):
        captured_totals.append(total)
        bar = mock.MagicMock()
        yield bar

    monkeypatch.setattr(
        "gha_downloader.downloader.alive_bar",
        _capturing_alive_bar,
    )
    monkeypatch.setattr(
        "gha_downloader.downloader.get_run_view",
        mock.Mock(return_value=mock_run_view),
    )
    monkeypatch.setattr(
        "gha_downloader.downloader.get_run_workflow_info",
        mock.Mock(return_value=mock_wf_info),
    )
    monkeypatch.setattr(
        "gha_downloader.downloader.get_workflow_yaml_content",
        mock.Mock(return_value=None),
    )
    monkeypatch.setattr(
        "gha_downloader.downloader.get_log_text",
        mock.Mock(return_value="log"),
    )
    monkeypatch.setattr(
        "gha_downloader.downloader.get_job_steps",
        mock.Mock(return_value=[]),
    )

    download_run(12345, repo="myorg/myrepo", output_dir=str(tmp_path), job_id=1)

    assert captured_totals == [1]


def test_download_run_bar_title_not_overwritten(monkeypatch, tmp_path):
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
    mock_wf_info = RunWorkflowInfo(
        path=".github/workflows/ci.yml",
        head_sha="abc123",
        referenced_workflows=[],
    )

    class _TrackingBar:
        def __init__(self):
            self.title_set_count = 0
            self.text_set_count = 0

        def __setattr__(self, name, value):
            if name in ("title_set_count", "text_set_count"):
                object.__setattr__(self, name, value)
                return
            if name == "title":
                self.title_set_count += 1
            if name == "text":
                self.text_set_count += 1

        def __call__(self):
            pass

    captured_bar = None

    @contextmanager
    def _capturing_alive_bar(total, *, title="", file=None, ctrl_c=False):
        nonlocal captured_bar
        captured_bar = _TrackingBar()
        yield captured_bar

    monkeypatch.setattr(
        "gha_downloader.downloader.alive_bar",
        _capturing_alive_bar,
    )
    monkeypatch.setattr(
        "gha_downloader.downloader.get_run_view",
        mock.Mock(return_value=mock_run_view),
    )
    monkeypatch.setattr(
        "gha_downloader.downloader.get_run_workflow_info",
        mock.Mock(return_value=mock_wf_info),
    )
    monkeypatch.setattr(
        "gha_downloader.downloader.get_workflow_yaml_content",
        mock.Mock(return_value=None),
    )
    monkeypatch.setattr(
        "gha_downloader.downloader.get_log_text",
        mock.Mock(return_value="log"),
    )
    monkeypatch.setattr(
        "gha_downloader.downloader.get_job_steps",
        mock.Mock(return_value=[]),
    )

    download_run(12345, repo="myorg/myrepo", output_dir=str(tmp_path))

    assert captured_bar is not None
    assert captured_bar.title_set_count == 0
    assert captured_bar.text_set_count >= 1


class TestExtractArtifactIds:
    def test_ids_present(self):
        log = "some line\nArtifact ID is 42\nother line\nArtifact ID is 99\n"
        result = _extract_artifact_ids(log)
        assert result == [42, 99]

    def test_deduplicates(self):
        log = "Artifact ID is 10\nArtifact ID is 10\nArtifact ID is 20\n"
        result = _extract_artifact_ids(log)
        assert result == [10, 20]

    def test_no_matches(self):
        log = "no artifact lines here\njust regular log\n"
        result = _extract_artifact_ids(log)
        assert result == []
