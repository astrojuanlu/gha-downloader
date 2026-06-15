from pathlib import Path
from unittest import mock

import pytest

from gha_downloader.downloader import (
    DownloaderError,
    _extract_artifact_ids,
    _split_log_by_groups,
    download_all_jobs_from_run,
    download_artifact,
    download_failed_jobs,
    download_job,
    download_run,
    get_run_info,
    list_artifacts,
)
from gha_downloader.gh import (
    ArtifactData,
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


def test_download_run_force_success_replaces_atomically(monkeypatch, tmp_path):
    run_dir = tmp_path / "12345"
    run_dir.mkdir(parents=True)
    (run_dir / "stale.txt").write_text("stale data")

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

    assert not (run_dir / "stale.txt").exists()
    assert (run_dir / "run.json").exists()
    assert not list(tmp_path.glob("12345.tmp*"))


def test_download_run_force_failure_preserves_original(monkeypatch, tmp_path):
    run_dir = tmp_path / "12345"
    run_dir.mkdir(parents=True)
    (run_dir / "existing.txt").write_text("original data")

    monkeypatch.setattr(
        "gha_downloader.downloader.get_run_view",
        mock.Mock(side_effect=GhNotFoundError("not found")),
    )

    with pytest.raises(GhNotFoundError):
        download_run(12345, repo="myorg/myrepo", output_dir=str(tmp_path), force=True)

    assert (run_dir / "existing.txt").exists()
    assert (run_dir / "existing.txt").read_text() == "original data"

    assert not list(tmp_path.glob("12345.tmp*"))


def test_download_run_removes_dir_on_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "gha_downloader.downloader.get_run_view",
        mock.Mock(side_effect=GhAutoDetectError("Cannot auto-detect repository.")),
    )

    with pytest.raises(GhAutoDetectError):
        download_run(12345, repo=None, output_dir=str(tmp_path))

    run_dir = tmp_path / "12345"
    assert not run_dir.exists()


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


def _make_run_view(
    *,
    jobs: list[JobData] | None = None,
) -> RunViewData:
    return RunViewData(
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
        jobs=jobs,
    )


class TestGetRunInfoService:
    def test_job_slug_present(self, monkeypatch):
        mock_data = _make_run_view(
            jobs=[
                JobData(
                    databaseId=42,
                    name="Test Job",
                    status="completed",
                    conclusion="success",
                    startedAt="2024-01-01T00:00:00Z",
                    completedAt="2024-01-01T00:01:00Z",
                )
            ]
        )
        monkeypatch.setattr(
            "gha_downloader.downloader.get_run_view",
            mock.Mock(return_value=mock_data),
        )
        result = get_run_info(12345, repo="org/repo")
        assert result["jobs"][0]["job_slug"] == "test-job"

    def test_include_steps(self, monkeypatch):
        mock_data = _make_run_view(
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
                            name="Run Tests",
                            status="completed",
                            conclusion="success",
                            number=7,
                            startedAt="2024-01-01T00:00:00Z",
                            completedAt="2024-01-01T00:00:30Z",
                        )
                    ],
                )
            ]
        )
        monkeypatch.setattr(
            "gha_downloader.downloader.get_run_view",
            mock.Mock(return_value=mock_data),
        )
        result = get_run_info(12345, repo="org/repo", include_steps=True)
        assert result["jobs"][0]["steps"][0]["step_label"] == "07_run-tests"

    def test_only_failed(self, monkeypatch):
        mock_data = _make_run_view(
            jobs=[
                JobData(
                    databaseId=1,
                    name="pass-job",
                    status="completed",
                    conclusion="success",
                    startedAt="2024-01-01T00:00:00Z",
                    completedAt="2024-01-01T00:01:00Z",
                ),
                JobData(
                    databaseId=3,
                    name="fail-job",
                    status="completed",
                    conclusion="failure",
                    startedAt="2024-01-01T00:00:00Z",
                    completedAt="2024-01-01T00:01:00Z",
                ),
            ]
        )
        monkeypatch.setattr(
            "gha_downloader.downloader.get_run_view",
            mock.Mock(return_value=mock_data),
        )
        result = get_run_info(12345, repo="org/repo", only_failed=True)
        assert len(result["jobs"]) == 1
        assert result["jobs"][0]["job_slug"] == "fail-job"

    def test_error_raises_downloader_error(self, monkeypatch):
        monkeypatch.setattr(
            "gha_downloader.downloader.get_run_view",
            mock.Mock(side_effect=GhNotFoundError("not found")),
        )
        with pytest.raises(DownloaderError, match="not found"):
            get_run_info(99999, repo="org/repo")


class TestListArtifactsService:
    def test_artifact_slug_present(self, monkeypatch):
        art = ArtifactData.model_validate(
            {"id": 100, "name": "My Artifact", "size_in_bytes": 2048, "expired": False}
        )
        monkeypatch.setattr(
            "gha_downloader.downloader.get_artifacts",
            mock.Mock(return_value=[art]),
        )
        result = list_artifacts(12345, repo="org/repo")
        assert result[0]["artifact_slug"] == "my-artifact"

    def test_only_available_filters_expired(self, monkeypatch):
        art1 = ArtifactData.model_validate(
            {"id": 100, "name": "fresh", "size_in_bytes": 1024, "expired": False}
        )
        art2 = ArtifactData.model_validate(
            {"id": 200, "name": "old", "size_in_bytes": 512, "expired": True}
        )
        monkeypatch.setattr(
            "gha_downloader.downloader.get_artifacts",
            mock.Mock(return_value=[art1, art2]),
        )
        result = list_artifacts(12345, repo="org/repo", only_available=True)
        assert len(result) == 1
        assert result[0]["name"] == "fresh"

    def test_job_id_filtering(self, monkeypatch):
        art1 = ArtifactData.model_validate(
            {"id": 100, "name": "test-results", "size_in_bytes": 2048, "expired": False}
        )
        art2 = ArtifactData.model_validate(
            {"id": 200, "name": "build-logs", "size_in_bytes": 1024, "expired": False}
        )
        monkeypatch.setattr(
            "gha_downloader.downloader.get_artifacts",
            mock.Mock(return_value=[art1, art2]),
        )
        monkeypatch.setattr(
            "gha_downloader.downloader.get_log_text",
            mock.Mock(return_value="Artifact ID is 100\n"),
        )
        result = list_artifacts(12345, repo="org/repo", job_id=42)
        assert len(result) == 1
        assert result[0]["name"] == "test-results"

    def test_error_raises_downloader_error(self, monkeypatch):
        monkeypatch.setattr(
            "gha_downloader.downloader.get_artifacts",
            mock.Mock(side_effect=GhNotFoundError("not found")),
        )
        with pytest.raises(DownloaderError, match="not found"):
            list_artifacts(99999, repo="org/repo")

    def test_name_contains_filter(self, monkeypatch):
        art1 = ArtifactData.model_validate(
            {"id": 100, "name": "test-results", "size_in_bytes": 2048, "expired": False}
        )
        art2 = ArtifactData.model_validate(
            {"id": 200, "name": "build-logs", "size_in_bytes": 1024, "expired": False}
        )
        monkeypatch.setattr(
            "gha_downloader.downloader.get_artifacts",
            mock.Mock(return_value=[art1, art2]),
        )
        result = list_artifacts(12345, repo="org/repo", name_contains="test")
        assert len(result) == 1
        assert result[0]["name"] == "test-results"

    def test_name_contains_case_insensitive(self, monkeypatch):
        art = ArtifactData.model_validate(
            {"id": 100, "name": "Test-Results", "size_in_bytes": 2048, "expired": False}
        )
        monkeypatch.setattr(
            "gha_downloader.downloader.get_artifacts",
            mock.Mock(return_value=[art]),
        )
        result = list_artifacts(12345, repo="org/repo", name_contains="TEST")
        assert len(result) == 1

    def test_name_contains_no_match(self, monkeypatch):
        art = ArtifactData.model_validate(
            {"id": 100, "name": "test-results", "size_in_bytes": 2048, "expired": False}
        )
        monkeypatch.setattr(
            "gha_downloader.downloader.get_artifacts",
            mock.Mock(return_value=[art]),
        )
        result = list_artifacts(12345, repo="org/repo", name_contains="missing")
        assert result == []


class TestDownloadJobService:
    def test_returns_path(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "gha_downloader.downloader.download_run",
            mock.Mock(),
        )
        result = download_job(
            12345,
            job_id=42,
            repo="org/repo",
            output_dir=str(tmp_path),
        )
        assert result == tmp_path / "12345"

    def test_additive_second_call_preserves_other_jobs(self, monkeypatch, tmp_path):
        run_dir = tmp_path / "12345"
        run_dir.mkdir()
        existing_job_dir = run_dir / "logs" / "existing-job"
        existing_job_dir.mkdir(parents=True)
        (existing_job_dir / "full.log").write_text("old log")
        monkeypatch.setattr(
            "gha_downloader.downloader.download_run",
            mock.Mock(),
        )
        download_job(
            12345,
            job_id=42,
            output_dir=str(tmp_path),
        )
        assert (existing_job_dir / "full.log").exists()

    def test_force_removes_only_target_job_dir(self, monkeypatch, tmp_path):
        run_dir = tmp_path / "12345"
        run_dir.mkdir()
        other_job_dir = run_dir / "logs" / "other-job"
        other_job_dir.mkdir(parents=True)
        (other_job_dir / "full.log").write_text("other log")
        monkeypatch.setattr(
            "gha_downloader.downloader.download_run",
            mock.Mock(),
        )
        download_job(
            12345,
            job_id=42,
            output_dir=str(tmp_path),
            force=True,
        )
        assert (other_job_dir / "full.log").exists()

    def test_no_output_dir_raises_type_error(self):
        with pytest.raises(TypeError, match="output_dir"):
            download_job(12345, job_id=42)

    def test_gh_error_raises_downloader_error(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "gha_downloader.downloader.download_run",
            mock.Mock(side_effect=GhNotFoundError("not found")),
        )
        with pytest.raises(DownloaderError, match="not found"):
            download_job(
                12345,
                job_id=42,
                repo="org/repo",
                output_dir=str(tmp_path),
            )


class TestDownloadAllJobsFromRunService:
    def test_returns_path(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "gha_downloader.downloader.download_run",
            mock.Mock(),
        )
        result = download_all_jobs_from_run(
            12345,
            repo="org/repo",
            output_dir=str(tmp_path),
        )
        assert result == tmp_path / "12345"

    def test_force_false_caches(self, tmp_path):
        run_dir = tmp_path / "12345"
        run_dir.mkdir()
        result = download_all_jobs_from_run(
            12345,
            output_dir=str(tmp_path),
        )
        assert result == run_dir

    def test_no_output_dir_raises_type_error(self):
        with pytest.raises(TypeError, match="output_dir"):
            download_all_jobs_from_run(12345)


class TestDownloadArtifactService:
    def test_success(self, monkeypatch, tmp_path):
        run_dir = tmp_path / "12345"
        run_dir.mkdir()
        art = ArtifactData.model_validate(
            {"id": 100, "name": "my-art", "size_in_bytes": 2048, "expired": False}
        )
        monkeypatch.setattr(
            "gha_downloader.downloader.get_artifacts",
            mock.Mock(return_value=[art]),
        )
        monkeypatch.setattr(
            "gha_downloader.downloader._gh_download_artifact",
            mock.Mock(),
        )
        result = download_artifact(
            12345,
            artifact_slug="my-art",
            output_dir=str(tmp_path),
        )
        assert "my-art" in str(result)

    def test_auto_creates_run_dir(self, monkeypatch, tmp_path):
        art = ArtifactData.model_validate(
            {"id": 100, "name": "my-art", "size_in_bytes": 2048, "expired": False}
        )
        monkeypatch.setattr(
            "gha_downloader.downloader.get_artifacts",
            mock.Mock(return_value=[art]),
        )
        monkeypatch.setattr(
            "gha_downloader.downloader._gh_download_artifact",
            mock.Mock(),
        )
        result = download_artifact(
            12345,
            artifact_slug="my-art",
            output_dir=str(tmp_path),
        )
        assert (tmp_path / "12345").is_dir()
        assert "my-art" in str(result)

    def test_slug_not_found(self, monkeypatch, tmp_path):
        run_dir = tmp_path / "12345"
        run_dir.mkdir()
        art = ArtifactData.model_validate(
            {"id": 100, "name": "other-art", "size_in_bytes": 2048, "expired": False}
        )
        monkeypatch.setattr(
            "gha_downloader.downloader.get_artifacts",
            mock.Mock(return_value=[art]),
        )
        with pytest.raises(DownloaderError, match="not found"):
            download_artifact(
                12345,
                artifact_slug="missing",
                output_dir=str(tmp_path),
            )

    def test_expired(self, monkeypatch, tmp_path):
        run_dir = tmp_path / "12345"
        run_dir.mkdir()
        art = ArtifactData.model_validate(
            {"id": 200, "name": "old-art", "size_in_bytes": 1024, "expired": True}
        )
        monkeypatch.setattr(
            "gha_downloader.downloader.get_artifacts",
            mock.Mock(return_value=[art]),
        )
        with pytest.raises(DownloaderError, match="expired"):
            download_artifact(
                12345,
                artifact_slug="old-art",
                output_dir=str(tmp_path),
            )

    def test_already_exists_without_force(self, monkeypatch, tmp_path):
        run_dir = tmp_path / "12345"
        run_dir.mkdir()
        art_dir = run_dir / "artifacts" / "my-art"
        art_dir.mkdir(parents=True)
        (art_dir / "existing.txt").write_text("data")
        art = ArtifactData.model_validate(
            {"id": 100, "name": "my-art", "size_in_bytes": 2048, "expired": False}
        )
        monkeypatch.setattr(
            "gha_downloader.downloader.get_artifacts",
            mock.Mock(return_value=[art]),
        )
        with pytest.raises(DownloaderError, match="force=True"):
            download_artifact(
                12345,
                artifact_slug="my-art",
                output_dir=str(tmp_path),
            )

    def test_force_replaces(self, monkeypatch, tmp_path):
        run_dir = tmp_path / "12345"
        run_dir.mkdir()
        art_dir = run_dir / "artifacts" / "my-art"
        art_dir.mkdir(parents=True)
        (art_dir / "stale.txt").write_text("stale")
        art = ArtifactData.model_validate(
            {"id": 100, "name": "my-art", "size_in_bytes": 2048, "expired": False}
        )
        monkeypatch.setattr(
            "gha_downloader.downloader.get_artifacts",
            mock.Mock(return_value=[art]),
        )
        monkeypatch.setattr(
            "gha_downloader.downloader._gh_download_artifact",
            mock.Mock(),
        )
        download_artifact(
            12345,
            artifact_slug="my-art",
            output_dir=str(tmp_path),
            force=True,
        )
        assert not (art_dir / "stale.txt").exists()


class TestDownloadFailedJobsService:
    def test_downloads_failed_jobs(self, monkeypatch, tmp_path):
        info = {
            "jobs": [
                {"databaseId": 1, "job_slug": "fail-job", "conclusion": "failure"},
            ]
        }
        monkeypatch.setattr(
            "gha_downloader.downloader.get_run_info",
            mock.Mock(return_value=info),
        )
        monkeypatch.setattr(
            "gha_downloader.downloader.download_run",
            mock.Mock(),
        )
        run_dir, slugs = download_failed_jobs(
            12345,
            repo="org/repo",
            output_dir=str(tmp_path),
        )
        assert run_dir == tmp_path / "12345"
        assert slugs == ["fail-job"]

    def test_no_failures_returns_empty(self, monkeypatch, tmp_path):
        info = {"jobs": []}
        monkeypatch.setattr(
            "gha_downloader.downloader.get_run_info",
            mock.Mock(return_value=info),
        )
        run_dir, slugs = download_failed_jobs(
            12345,
            repo="org/repo",
            output_dir=str(tmp_path),
        )
        assert slugs == []

    def test_run_not_found_raises(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "gha_downloader.downloader.get_run_info",
            mock.Mock(side_effect=DownloaderError("not found")),
        )
        with pytest.raises(DownloaderError, match="not found"):
            download_failed_jobs(
                99999,
                repo="org/repo",
                output_dir=str(tmp_path),
            )

    def test_additive_loop_preserves_previous_jobs(self, monkeypatch, tmp_path):
        info = {
            "jobs": [
                {
                    "databaseId": 1,
                    "job_slug": "fail-one",
                    "conclusion": "failure",
                },
                {
                    "databaseId": 2,
                    "job_slug": "fail-two",
                    "conclusion": "failure",
                },
            ]
        }
        call_count = 0

        def _fake_download_run(
            run_id, repo=None, job_id=None, output_dir=None, force=False
        ):
            nonlocal call_count
            call_count += 1
            run_dir = Path(output_dir) / str(run_id)
            run_dir.mkdir(parents=True, exist_ok=True)
            slug = "fail-one" if job_id == 1 else "fail-two"
            job_dir = run_dir / "logs" / slug
            job_dir.mkdir(parents=True, exist_ok=True)
            (job_dir / "full.log").write_text(f"job {job_id} log")

        monkeypatch.setattr(
            "gha_downloader.downloader.get_run_info",
            mock.Mock(return_value=info),
        )
        monkeypatch.setattr(
            "gha_downloader.downloader.download_run",
            _fake_download_run,
        )
        run_dir, slugs = download_failed_jobs(
            12345,
            repo="org/repo",
            output_dir=str(tmp_path),
        )
        assert call_count == 2
        assert (run_dir / "logs" / "fail-one" / "full.log").exists()
        assert (run_dir / "logs" / "fail-two" / "full.log").exists()
