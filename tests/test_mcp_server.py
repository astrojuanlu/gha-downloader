import asyncio
import inspect
from pathlib import Path
from unittest import mock

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from gha_downloader.gh import (
    ArtifactData,
    GhApiError,
    GhNotFoundError,
    JobData,
    RunViewData,
    StepData,
)
from gha_downloader.mcp_server import (
    _default_output_dir,
    download_artifact,
    download_failed_jobs,
    download_job,
    get_run_info,
    list_artifact_files,
    list_artifacts,
    list_logs,
    list_run_files,
    read_artifact_file,
    read_log_file,
    search_log,
)


def _run(coro):
    return asyncio.run(coro)


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


class TestGetRunInfo:
    def test_success(self, monkeypatch):
        mock_data = _make_run_view(
            jobs=[
                JobData(
                    databaseId=42,
                    name="test-job",
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

        result = _run(get_run_info(12345, repo="org/repo"))
        assert result["databaseId"] == 12345
        assert len(result["jobs"]) == 1

    def test_not_found(self, monkeypatch):
        monkeypatch.setattr(
            "gha_downloader.downloader.get_run_view",
            mock.Mock(side_effect=GhNotFoundError("not found")),
        )

        with pytest.raises(ToolError, match="not found"):
            _run(get_run_info(99999, repo="org/repo"))

    def test_api_error(self, monkeypatch):
        monkeypatch.setattr(
            "gha_downloader.downloader.get_run_view",
            mock.Mock(side_effect=GhApiError("api error")),
        )

        with pytest.raises(ToolError, match="api error"):
            _run(get_run_info(12345, repo="org/repo"))


class TestListArtifacts:
    def test_artifacts_present(self, monkeypatch):
        art = ArtifactData.model_validate(
            {"id": 100, "name": "my-artifact", "size_in_bytes": 2048, "expired": False}
        )
        monkeypatch.setattr(
            "gha_downloader.downloader.get_artifacts",
            mock.Mock(return_value=[art]),
        )

        result = _run(list_artifacts(12345, repo="org/repo"))
        assert len(result) == 1
        assert result[0]["name"] == "my-artifact"

    def test_no_artifacts(self, monkeypatch):
        monkeypatch.setattr(
            "gha_downloader.downloader.get_artifacts",
            mock.Mock(return_value=[]),
        )

        result = _run(list_artifacts(12345, repo="org/repo"))
        assert result == []

    def test_not_found_raises_tool_error(self, monkeypatch):
        monkeypatch.setattr(
            "gha_downloader.downloader.get_artifacts",
            mock.Mock(side_effect=GhNotFoundError("not found")),
        )

        with pytest.raises(ToolError, match="not found"):
            _run(list_artifacts(12345, repo="org/repo"))

    def test_expired_artifact_included(self, monkeypatch):
        art = ArtifactData.model_validate(
            {"id": 200, "name": "old-artifact", "size_in_bytes": 1024, "expired": True}
        )
        monkeypatch.setattr(
            "gha_downloader.downloader.get_artifacts",
            mock.Mock(return_value=[art]),
        )

        result = _run(list_artifacts(12345, repo="org/repo", only_available=False))
        assert result[0]["expired"] is True

    def test_artifact_slug_present(self, monkeypatch):
        art = ArtifactData.model_validate(
            {"id": 100, "name": "My Artifact", "size_in_bytes": 2048, "expired": False}
        )
        monkeypatch.setattr(
            "gha_downloader.downloader.get_artifacts",
            mock.Mock(return_value=[art]),
        )

        result = _run(list_artifacts(12345, repo="org/repo"))
        assert result[0]["artifact_slug"] == "my-artifact"

    def test_job_id_filters_artifacts(self, monkeypatch):
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

        result = _run(list_artifacts(12345, repo="org/repo", job_id=42))
        assert len(result) == 1
        assert result[0]["name"] == "test-results"

    def test_job_id_no_artifact_ids_returns_empty(self, monkeypatch):
        art1 = ArtifactData.model_validate(
            {"id": 100, "name": "test-results", "size_in_bytes": 2048, "expired": False}
        )
        monkeypatch.setattr(
            "gha_downloader.downloader.get_artifacts",
            mock.Mock(return_value=[art1]),
        )
        monkeypatch.setattr(
            "gha_downloader.downloader.get_log_text",
            mock.Mock(return_value="no artifact lines\n"),
        )

        result = _run(list_artifacts(12345, repo="org/repo", job_id=42))
        assert result == []

    def test_no_job_id_returns_all(self, monkeypatch):
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

        result = _run(list_artifacts(12345, repo="org/repo"))
        assert len(result) == 2

    def test_expired_excluded_by_default(self, monkeypatch):
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

        result = _run(list_artifacts(12345, repo="org/repo"))
        assert len(result) == 1
        assert result[0]["name"] == "fresh"

    def test_expired_included_with_only_available_false(self, monkeypatch):
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

        result = _run(list_artifacts(12345, repo="org/repo", only_available=False))
        assert len(result) == 2

    def test_no_arg_returns_available_only(self, monkeypatch):
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

        result = _run(list_artifacts(12345, repo="org/repo"))
        names = [r["name"] for r in result]
        assert "fresh" in names
        assert "old" not in names

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

        result = _run(list_artifacts(12345, repo="org/repo", name_contains="test"))
        assert len(result) == 1
        assert result[0]["name"] == "test-results"

    def test_name_contains_no_match(self, monkeypatch):
        art = ArtifactData.model_validate(
            {"id": 100, "name": "test-results", "size_in_bytes": 2048, "expired": False}
        )
        monkeypatch.setattr(
            "gha_downloader.downloader.get_artifacts",
            mock.Mock(return_value=[art]),
        )

        result = _run(list_artifacts(12345, repo="org/repo", name_contains="missing"))
        assert result == []


class TestDownloadJob:
    def test_success(self, monkeypatch):
        monkeypatch.setattr(
            "gha_downloader.downloader.download_run",
            mock.Mock(),
        )

        result = _run(download_job(12345, job_id=42, repo="org/repo"))
        assert "12345" in result

    def test_force_redownload(self, tmp_path, monkeypatch):
        run_dir = tmp_path / "12345"
        run_dir.mkdir()
        monkeypatch.setattr(
            "gha_downloader.downloader.download_run",
            mock.Mock(),
        )

        result = _run(
            download_job(
                12345,
                job_id=42,
                output_dir=str(tmp_path),
                force=True,
            )
        )
        assert "12345" in result

    def test_gh_error(self, monkeypatch):
        monkeypatch.setattr(
            "gha_downloader.downloader.download_run",
            mock.Mock(side_effect=GhNotFoundError("not found")),
        )

        with pytest.raises(ToolError, match="not found"):
            _run(download_job(12345, job_id=42, repo="org/repo"))


class TestListRunFiles:
    def test_run_downloaded(self, tmp_path):
        run_dir = tmp_path / "12345"
        run_dir.mkdir()
        (run_dir / "run.json").write_text("{}")
        logs_dir = run_dir / "logs" / "test-job"
        logs_dir.mkdir(parents=True)
        (logs_dir / "full.log").write_text("log")
        (logs_dir / "01_checkout.txt").write_text("step")
        art_dir = run_dir / "artifacts" / "my-artifact"
        art_dir.mkdir(parents=True)
        (art_dir / "result.txt").write_text("data")

        result = list_run_files(12345, output_dir=str(tmp_path))
        lines = result.split("\n")
        assert "run.json" in lines
        assert any("full.log" in line for line in lines)
        assert any("result.txt" in line for line in lines)

    def test_run_not_downloaded(self, tmp_path):
        with pytest.raises(ToolError, match="does not exist"):
            list_run_files(99999, output_dir=str(tmp_path))


class TestListLogs:
    def test_list_job_slugs(self, tmp_path):
        run_dir = tmp_path / "12345" / "logs"
        (run_dir / "build-job").mkdir(parents=True)
        (run_dir / "test-job").mkdir(parents=True)

        result = list_logs(12345, output_dir=str(tmp_path))
        assert "build-job" in result
        assert "test-job" in result

    def test_step_labels_in_listing(self, tmp_path):
        run_dir = tmp_path / "12345" / "logs" / "build-job"
        run_dir.mkdir(parents=True)
        (run_dir / "01_checkout.txt").write_text("checkout output")
        (run_dir / "02_build.txt").write_text("build output")
        (run_dir / "full.log").write_text("full log")

        result = list_logs(12345, output_dir=str(tmp_path))
        assert "build-job" in result
        assert "steps:" in result
        assert "01_checkout" in result
        assert "02_build" in result

    def test_no_logs_directory(self, tmp_path):
        with pytest.raises(ToolError, match="No logs directory"):
            list_logs(12345, output_dir=str(tmp_path))

    def test_header_line_is_run_dir_path(self, tmp_path):
        run_dir = tmp_path / "12345" / "logs" / "build-job"
        run_dir.mkdir(parents=True)
        (run_dir / "full.log").write_text("log")

        result = list_logs(12345, output_dir=str(tmp_path))
        first_line = result.split("\n")[0]
        assert str((tmp_path / "12345").resolve()) == first_line


class TestDownloadArtifact:
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

        result = _run(
            download_artifact(
                12345,
                artifact_slug="my-art",
                output_dir=str(tmp_path),
            )
        )
        assert (tmp_path / "12345").is_dir()
        assert "my-art" in result

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

        with pytest.raises(ToolError, match="not found"):
            _run(
                download_artifact(
                    12345,
                    artifact_slug="missing-slug",
                    output_dir=str(tmp_path),
                )
            )

    def test_expired_artifact(self, monkeypatch, tmp_path):
        run_dir = tmp_path / "12345"
        run_dir.mkdir()
        art = ArtifactData.model_validate(
            {"id": 200, "name": "old-art", "size_in_bytes": 1024, "expired": True}
        )
        monkeypatch.setattr(
            "gha_downloader.downloader.get_artifacts",
            mock.Mock(return_value=[art]),
        )

        with pytest.raises(ToolError, match="expired"):
            _run(
                download_artifact(
                    12345,
                    artifact_slug="old-art",
                    output_dir=str(tmp_path),
                )
            )

    def test_redownload_without_force_raises(self, monkeypatch, tmp_path):
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

        with pytest.raises(ToolError, match="force=True"):
            _run(
                download_artifact(
                    12345,
                    artifact_slug="my-art",
                    output_dir=str(tmp_path),
                )
            )

    def test_redownload_with_force_succeeds(self, monkeypatch, tmp_path):
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

        result = _run(
            download_artifact(
                12345,
                artifact_slug="my-art",
                output_dir=str(tmp_path),
                force=True,
            )
        )
        assert "my-art" in result
        assert not (art_dir / "stale.txt").exists()


class TestReadArtifactFile:
    def test_read_text_file(self, tmp_path):
        art_dir = tmp_path / "12345" / "artifacts" / "my-artifact"
        art_dir.mkdir(parents=True)
        (art_dir / "result.txt").write_text("artifact data", encoding="utf-8")

        result = read_artifact_file(
            12345,
            artifact_slug="my-artifact",
            file_path="result.txt",
            output_dir=str(tmp_path),
        )
        assert result.startswith("# Lines 1–1 of 1")
        assert "artifact data" in result

    def test_binary_file(self, tmp_path):
        art_dir = tmp_path / "12345" / "artifacts" / "my-artifact"
        art_dir.mkdir(parents=True)
        (art_dir / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n")

        with pytest.raises(ToolError, match="binary"):
            read_artifact_file(
                12345,
                artifact_slug="my-artifact",
                file_path="image.png",
                output_dir=str(tmp_path),
            )

    def test_artifact_not_found(self, tmp_path):
        run_dir = tmp_path / "12345" / "artifacts"
        run_dir.mkdir(parents=True)

        with pytest.raises(ToolError, match="not found"):
            read_artifact_file(
                12345,
                artifact_slug="missing-artifact",
                file_path="result.txt",
                output_dir=str(tmp_path),
            )

    def test_file_not_found_in_artifact(self, tmp_path):
        art_dir = tmp_path / "12345" / "artifacts" / "my-artifact"
        art_dir.mkdir(parents=True)
        (art_dir / "other.txt").write_text("data")

        with pytest.raises(ToolError, match="not found"):
            read_artifact_file(
                12345,
                artifact_slug="my-artifact",
                file_path="missing.txt",
                output_dir=str(tmp_path),
            )

    def test_ansi_stripped_by_default(self, tmp_path):
        art_dir = tmp_path / "12345" / "artifacts" / "my-artifact"
        art_dir.mkdir(parents=True)
        (art_dir / "log.txt").write_text("\x1b[31mError\x1b[0m: something failed\n")

        result = read_artifact_file(
            12345,
            artifact_slug="my-artifact",
            file_path="log.txt",
            output_dir=str(tmp_path),
        )
        assert result.startswith("# Lines 1–1 of 1")
        assert "Error: something failed" in result

    def test_ansi_preserved_with_raw_true(self, tmp_path):
        art_dir = tmp_path / "12345" / "artifacts" / "my-artifact"
        art_dir.mkdir(parents=True)
        raw_content = "\x1b[31mError\x1b[0m: something failed\n"
        (art_dir / "log.txt").write_text(raw_content)

        result = read_artifact_file(
            12345,
            artifact_slug="my-artifact",
            file_path="log.txt",
            output_dir=str(tmp_path),
            raw=True,
        )
        assert "\x1b[31mError\x1b[0m" in result

    def test_no_ansi_unaffected(self, tmp_path):
        art_dir = tmp_path / "12345" / "artifacts" / "my-artifact"
        art_dir.mkdir(parents=True)
        (art_dir / "clean.txt").write_text("clean content\n")

        result = read_artifact_file(
            12345,
            artifact_slug="my-artifact",
            file_path="clean.txt",
            output_dir=str(tmp_path),
        )
        assert "clean content" in result

    def test_pagination_returns_correct_range(self, tmp_path):
        art_dir = tmp_path / "12345" / "artifacts" / "my-artifact"
        art_dir.mkdir(parents=True)
        lines = [f"line{i}" for i in range(100)]
        (art_dir / "data.txt").write_text("\n".join(lines))

        result = read_artifact_file(
            12345,
            artifact_slug="my-artifact",
            file_path="data.txt",
            output_dir=str(tmp_path),
            offset=10,
            limit=5,
        )
        assert result.startswith("# Lines 11–15 of 100")
        assert "line10" in result
        assert "line14" in result

    def test_default_pagination_first_500_lines(self, tmp_path):
        art_dir = tmp_path / "12345" / "artifacts" / "my-artifact"
        art_dir.mkdir(parents=True)
        lines = [f"line{i}" for i in range(600)]
        (art_dir / "big.txt").write_text("\n".join(lines))

        result = read_artifact_file(
            12345,
            artifact_slug="my-artifact",
            file_path="big.txt",
            output_dir=str(tmp_path),
        )
        assert result.startswith("# Lines 1–500 of 600")
        assert "line0" in result
        assert "line499" in result


class TestReadLogFile:
    def test_read_full_log(self, tmp_path):
        job_dir = tmp_path / "12345" / "logs" / "build-job"
        job_dir.mkdir(parents=True)
        (job_dir / "full.log").write_text("line1\nline2\nline3\n")

        result = read_log_file(12345, job_slug="build-job", output_dir=str(tmp_path))
        assert result.startswith("# Lines 1–3 of 3")
        assert "line1" in result
        assert "line3" in result

    def test_read_step_file(self, tmp_path):
        job_dir = tmp_path / "12345" / "logs" / "build-job"
        job_dir.mkdir(parents=True)
        (job_dir / "full.log").write_text("full\n")
        (job_dir / "01_checkout.txt").write_text("checkout output\n")

        result = read_log_file(
            12345,
            job_slug="build-job",
            step_label="01_checkout",
            output_dir=str(tmp_path),
        )
        assert "checkout output" in result

    def test_pagination(self, tmp_path):
        job_dir = tmp_path / "12345" / "logs" / "build-job"
        job_dir.mkdir(parents=True)
        lines = [f"line{i}" for i in range(100)]
        (job_dir / "full.log").write_text("\n".join(lines))

        result = read_log_file(
            12345,
            job_slug="build-job",
            offset=10,
            limit=5,
            output_dir=str(tmp_path),
        )
        assert result.startswith("# Lines 11–15 of 100")
        assert "line10" in result
        assert "line14" in result

    def test_run_not_downloaded(self, tmp_path):
        with pytest.raises(ToolError, match="No logs directory"):
            read_log_file(12345, job_slug="build-job", output_dir=str(tmp_path))

    def test_bad_job_slug(self, tmp_path):
        job_dir = tmp_path / "12345" / "logs" / "real-job"
        job_dir.mkdir(parents=True)
        (job_dir / "full.log").write_text("log\n")

        with pytest.raises(ToolError, match="not found"):
            read_log_file(12345, job_slug="bad-job", output_dir=str(tmp_path))

    def test_bad_step_label(self, tmp_path):
        job_dir = tmp_path / "12345" / "logs" / "build-job"
        job_dir.mkdir(parents=True)
        (job_dir / "full.log").write_text("log\n")
        (job_dir / "01_checkout.txt").write_text("step\n")

        with pytest.raises(ToolError, match="not found"):
            read_log_file(
                12345,
                job_slug="build-job",
                step_label="99_missing",
                output_dir=str(tmp_path),
            )

    def test_ansi_stripped_by_default(self, tmp_path):
        job_dir = tmp_path / "12345" / "logs" / "build-job"
        job_dir.mkdir(parents=True)
        (job_dir / "full.log").write_text(
            "\x1b[31mError\x1b[0m: something failed\nall good\n"
        )

        result = read_log_file(12345, job_slug="build-job", output_dir=str(tmp_path))
        assert "Error: something failed" in result
        assert "\x1b[" not in result

    def test_ansi_preserved_with_raw_true(self, tmp_path):
        job_dir = tmp_path / "12345" / "logs" / "build-job"
        job_dir.mkdir(parents=True)
        (job_dir / "full.log").write_text("\x1b[31mError\x1b[0m: something failed\n")

        result = read_log_file(
            12345,
            job_slug="build-job",
            output_dir=str(tmp_path),
            raw=True,
        )
        assert "\x1b[31mError\x1b[0m" in result


class TestGetRunInfoJobSlug:
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

        result = _run(get_run_info(12345, repo="org/repo"))
        assert result["jobs"][0]["job_slug"] == "test-job"

    def test_steps_absent_by_default(self, monkeypatch):
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
                            name="checkout",
                            status="completed",
                            conclusion="success",
                            number=1,
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

        result = _run(get_run_info(12345, repo="org/repo"))
        assert "steps" not in result["jobs"][0]

    def test_steps_present_when_include_steps(self, monkeypatch):
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
                            name="checkout",
                            status="completed",
                            conclusion="success",
                            number=1,
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

        result = _run(get_run_info(12345, repo="org/repo", include_steps=True))
        assert "steps" in result["jobs"][0]
        assert result["jobs"][0]["steps"][0]["name"] == "checkout"

    def test_step_label_injected(self, monkeypatch):
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

        result = _run(get_run_info(12345, repo="org/repo", include_steps=True))
        assert result["jobs"][0]["steps"][0]["step_label"] == "07_run-tests"

    def test_skipped_steps_no_label(self, monkeypatch):
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
                            name="optional-step",
                            status="completed",
                            conclusion="skipped",
                            number=2,
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

        result = _run(get_run_info(12345, repo="org/repo", include_steps=True))
        assert "step_label" not in result["jobs"][0]["steps"][0]


class TestGetRunInfoOnlyFailed:
    def test_excludes_skipped(self, monkeypatch):
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
                    databaseId=2,
                    name="skip-job",
                    status="completed",
                    conclusion="skipped",
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

        result = _run(get_run_info(12345, repo="org/repo", only_failed=True))
        slugs = [j["job_slug"] for j in result["jobs"]]
        assert "fail-job" in slugs
        assert "pass-job" not in slugs
        assert "skip-job" not in slugs

    def test_includes_in_progress(self, monkeypatch):
        mock_data = _make_run_view(
            jobs=[
                JobData(
                    databaseId=1,
                    name="running-job",
                    status="in_progress",
                    conclusion=None,
                    startedAt="2024-01-01T00:00:00Z",
                ),
                JobData(
                    databaseId=2,
                    name="pass-job",
                    status="completed",
                    conclusion="success",
                    startedAt="2024-01-01T00:00:00Z",
                    completedAt="2024-01-01T00:01:00Z",
                ),
            ]
        )
        monkeypatch.setattr(
            "gha_downloader.downloader.get_run_view",
            mock.Mock(return_value=mock_data),
        )

        result = _run(get_run_info(12345, repo="org/repo", only_failed=True))
        slugs = [j["job_slug"] for j in result["jobs"]]
        assert "running-job" in slugs
        assert "pass-job" not in slugs


class TestSearchLog:
    def test_matching_lines(self, tmp_path):
        job_dir = tmp_path / "12345" / "logs" / "build-job"
        job_dir.mkdir(parents=True)
        (job_dir / "full.log").write_text("ok\nError: failed\nok\n")

        result = search_log(12345, "Error", output_dir=str(tmp_path))
        assert "build-job:2:" in result
        assert "Error: failed" in result

    def test_no_matches(self, tmp_path):
        job_dir = tmp_path / "12345" / "logs" / "build-job"
        job_dir.mkdir(parents=True)
        (job_dir / "full.log").write_text("all good\n")

        result = search_log(12345, "Error", output_dir=str(tmp_path))
        assert result == "No matches found."

    def test_invalid_regex(self, tmp_path):
        job_dir = tmp_path / "12345" / "logs" / "build-job"
        job_dir.mkdir(parents=True)
        (job_dir / "full.log").write_text("log\n")

        with pytest.raises(ToolError, match="Invalid regex"):
            search_log(12345, "[invalid", output_dir=str(tmp_path))

    def test_context_lines(self, tmp_path):
        job_dir = tmp_path / "12345" / "logs" / "build-job"
        job_dir.mkdir(parents=True)
        (job_dir / "full.log").write_text("line1\nline2\nError here\nline4\nline5\n")

        result = search_log(12345, "Error", output_dir=str(tmp_path), context_lines=1)
        lines = result.split("\n")
        assert any("build-job:2:" in line for line in lines)
        assert any("build-job:3:" in line for line in lines)
        assert any("build-job:4:" in line for line in lines)

    def test_run_not_downloaded(self, tmp_path):
        with pytest.raises(ToolError, match="No logs directory"):
            search_log(99999, "Error", output_dir=str(tmp_path))

    def test_max_results_truncates(self, tmp_path):
        job_dir = tmp_path / "12345" / "logs" / "build-job"
        job_dir.mkdir(parents=True)
        (job_dir / "full.log").write_text("Error1\nError2\nError3\n")

        result = search_log(12345, "Error", output_dir=str(tmp_path), max_results=2)
        assert "Error1" in result
        assert "Error2" in result
        assert "truncated" in result

    def test_max_results_not_reached(self, tmp_path):
        job_dir = tmp_path / "12345" / "logs" / "build-job"
        job_dir.mkdir(parents=True)
        (job_dir / "full.log").write_text("Error1\nError2\n")

        result = search_log(12345, "Error", output_dir=str(tmp_path), max_results=50)
        assert "truncated" not in result

    def test_ansi_stripped_from_matched_lines(self, tmp_path):
        job_dir = tmp_path / "12345" / "logs" / "build-job"
        job_dir.mkdir(parents=True)
        (job_dir / "full.log").write_text(
            "\x1b[31mError\x1b[0m: something failed\nok\n"
        )

        result = search_log(12345, "Error", output_dir=str(tmp_path))
        assert "Error: something failed" in result
        assert "\x1b[" not in result

    def test_job_not_downloaded_actionable_error(self, tmp_path):
        logs_dir = tmp_path / "12345" / "logs"
        logs_dir.mkdir(parents=True)
        (logs_dir / "other-job").mkdir()

        with pytest.raises(ToolError, match="has not been downloaded"):
            search_log(
                12345,
                "Error",
                job_slug="missing-job",
                output_dir=str(tmp_path),
            )

    def test_job_dir_present_but_no_full_log(self, tmp_path):
        logs_dir = tmp_path / "12345" / "logs"
        logs_dir.mkdir(parents=True)
        (logs_dir / "partial-job").mkdir()

        with pytest.raises(ToolError, match="full.log missing"):
            search_log(
                12345,
                "Error",
                job_slug="partial-job",
                output_dir=str(tmp_path),
            )

    def test_xdg_data_home_set(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        result = _default_output_dir()
        assert result == str(tmp_path / "gha-downloader" / "runs")

    def test_xdg_data_home_not_set(self, monkeypatch):
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        result = _default_output_dir()
        expected = str(Path.home() / ".local" / "share" / "gha-downloader" / "runs")
        assert result == expected


class TestAsyncToolDetection:
    def test_async_tools_are_coroutine_functions(self):
        assert inspect.iscoroutinefunction(get_run_info)
        assert inspect.iscoroutinefunction(list_artifacts)
        assert inspect.iscoroutinefunction(download_job)
        assert inspect.iscoroutinefunction(download_artifact)

    def test_sync_tools_are_not_coroutine_functions(self):
        assert not inspect.iscoroutinefunction(list_run_files)
        assert not inspect.iscoroutinefunction(list_logs)
        assert not inspect.iscoroutinefunction(read_log_file)
        assert not inspect.iscoroutinefunction(read_artifact_file)
        assert not inspect.iscoroutinefunction(search_log)
        assert not inspect.iscoroutinefunction(list_artifact_files)

    def test_download_failed_jobs_is_async(self):
        assert inspect.iscoroutinefunction(download_failed_jobs)


class TestDownloadFailedJobsMCP:
    def test_returns_path_and_slugs(self, monkeypatch, tmp_path):
        run_dir = tmp_path / "12345"
        monkeypatch.setattr(
            "gha_downloader.downloader.download_failed_jobs",
            mock.Mock(return_value=(run_dir, ["fail-job"])),
        )
        result = _run(
            download_failed_jobs(
                12345,
                output_dir=str(tmp_path),
            )
        )
        assert "fail-job" in result
        assert str(run_dir) in result

    def test_no_failures_message(self, monkeypatch, tmp_path):
        run_dir = tmp_path / "12345"
        monkeypatch.setattr(
            "gha_downloader.downloader.download_failed_jobs",
            mock.Mock(return_value=(run_dir, [])),
        )
        result = _run(
            download_failed_jobs(
                12345,
                output_dir=str(tmp_path),
            )
        )
        assert "No failed jobs" in result


class TestReadLogFileTail:
    def test_tail_returns_last_n_lines(self, tmp_path):
        job_dir = tmp_path / "12345" / "logs" / "build-job"
        job_dir.mkdir(parents=True)
        lines = [f"line{i}" for i in range(100)]
        (job_dir / "full.log").write_text("\n".join(lines))

        result = read_log_file(
            12345,
            job_slug="build-job",
            tail=10,
            output_dir=str(tmp_path),
        )
        assert result.startswith("# Lines 91–100 of 100")
        assert "line90" in result
        assert "line99" in result

    def test_tail_larger_than_file(self, tmp_path):
        job_dir = tmp_path / "12345" / "logs" / "build-job"
        job_dir.mkdir(parents=True)
        lines = [f"line{i}" for i in range(50)]
        (job_dir / "full.log").write_text("\n".join(lines))

        result = read_log_file(
            12345,
            job_slug="build-job",
            tail=500,
            output_dir=str(tmp_path),
        )
        assert result.startswith("# Lines 1–50 of 50")

    def test_tail_none_preserves_offset(self, tmp_path):
        job_dir = tmp_path / "12345" / "logs" / "build-job"
        job_dir.mkdir(parents=True)
        lines = [f"line{i}" for i in range(100)]
        (job_dir / "full.log").write_text("\n".join(lines))

        result = read_log_file(
            12345,
            job_slug="build-job",
            offset=10,
            limit=5,
            output_dir=str(tmp_path),
        )
        assert result.startswith("# Lines 11–15 of 100")

    def test_tail_overrides_offset(self, tmp_path):
        job_dir = tmp_path / "12345" / "logs" / "build-job"
        job_dir.mkdir(parents=True)
        lines = [f"line{i}" for i in range(100)]
        (job_dir / "full.log").write_text("\n".join(lines))

        result = read_log_file(
            12345,
            job_slug="build-job",
            offset=10,
            tail=5,
            output_dir=str(tmp_path),
        )
        assert result.startswith("# Lines 96–100 of 100")


class TestListArtifactFiles:
    def test_lists_files_in_artifact(self, tmp_path):
        art_dir = tmp_path / "12345" / "artifacts" / "my-artifact"
        art_dir.mkdir(parents=True)
        (art_dir / "result.txt").write_text("data")
        sub_dir = art_dir / "sub"
        sub_dir.mkdir(parents=True)
        (sub_dir / "nested.json").write_text("{}")

        result = list_artifact_files(
            12345,
            artifact_slug="my-artifact",
            output_dir=str(tmp_path),
        )
        assert "result.txt" in result
        assert "sub/nested.json" in result

    def test_artifact_not_downloaded(self, tmp_path):
        with pytest.raises(ToolError, match="does not exist"):
            list_artifact_files(
                12345,
                artifact_slug="missing",
                output_dir=str(tmp_path),
            )

    def test_empty_artifact_returns_empty_string(self, tmp_path):
        art_dir = tmp_path / "12345" / "artifacts" / "empty-art"
        art_dir.mkdir(parents=True)

        result = list_artifact_files(
            12345,
            artifact_slug="empty-art",
            output_dir=str(tmp_path),
        )
        assert result == ""
