# gha-downloader

![Warning: Vibe Coded](https://img.shields.io/badge/%E2%9A%A0%EF%B8%8F_warning-vibe_coded-orange?style=flat)

Download logs and artifacts from GitHub Actions runs for offline inspection.

## Installation

The `gh` CLI must be installed and proper `gh auth` must be available with the `repo` read scope.

```sh
uv tool install "gha-downloader @ git+https://github.com/astrojuanlu/gha-downloader.git"
```

## Usage

### `gha-download` — Quick shortcut

Downloads all job logs for a run:

```sh
$ gh auth login
$ gha-download 27357958065 81019475171 --repo canonical/mysql-operators
$ ls runs/27357958065/
logs/  run.json
```

You can also pass a full GitHub Actions URL (the `--repo` is inferred):

```sh
$ gha-download https://github.com/canonical/mysql-operators/actions/runs/27357958065/job/81019475171
```

```
gha-download [-h] [--repo REPO] [--dir DIR] [-v] run_id job_id
```

| Flag     | Default  | Description                                         |
|----------|----------|-----------------------------------------------------|
| `RUN_ID` | required | Numeric workflow run ID or full Actions URL         |
| `JOB_ID` | required | Numeric job ID (if numerical run ID was given)      |
| `--repo` | auto     | Repository in `ORG/REPO` format (inferred from URL) |
| `--dir`  | `./runs` | Root directory for downloads                        |
| `-v`     | 0        | Verbosity: `-v` INFO, `-vv` DEBUG                   |

### `gha-downloader` — Full subcommand CLI

```sh
$ gha-downloader run show 27357958065 --repo canonical/mysql-operators
$ gha-downloader run download 27357958065 --repo canonical/mysql-operators
$ gha-downloader job download 27357958065 80847830020 --repo canonical/mysql-operators
$ gha-downloader artifact list 27357958065 --repo canonical/mysql-operators
$ gha-downloader artifact download 27357958065 "test-results" --repo canonical/mysql-operators
```

| Subcommand              | Description                                                |
|-------------------------|------------------------------------------------------------|
| `run show RUN_ID`       | Print run metadata as JSON. `--include-steps`, `--only-failed` |
| `run download RUN_ID`   | Download all job logs. `--dir`, `--force`                  |
| `job download RUN_ID JOB_ID` | Download a single job's logs. `--dir`, `--force`      |
| `artifact list RUN_ID`  | List artifacts. `--job-id`, `--all` (include expired)      |
| `artifact download RUN_ID NAME` | Download an artifact by name. `--dir`               |

### Repo inference

There are two repo-inference paths:

1. **URL-path extraction** — when you pass a full GitHub Actions URL, `ORG/REPO` is extracted from the `github.com/ORG/REPO/actions/` segment. This works regardless of your current working directory.
2. **git-remote detection** — when you pass a numeric run ID without `--repo`, the CLI auto-detects the repo from the `.git` remote. This only works when running from inside a clone of the target repository.

For cross-repo downloads, pass `--repo ORG/REPO` explicitly.

### On-disk layout

```
runs/27357958065/
  run.json                          # workflow run metadata
  logs/
    <job-slug>/
      full.log                      # raw job log from the GitHub API
      01_set-up-job.txt             # per-step .txt files are non-overlapping
      02_checkout.txt               #   partitions of full.log
      ...
  artifacts/                        # only present when downloaded explicitly
    <artifact-slug>/
      ...                           # extracted contents
```

Step numbers may skip (skipped steps produce no file).

### Exit codes

| Code | Meaning          |
|------|------------------|
| 0    | Success          |
| 1    | Internal error   |
| 2    | User error       |
| 3    | Network error    |
| 130  | Interrupted      |

## MCP Server

An MCP server is available for AI agents to inspect GitHub Actions runs programmatically.

### Installation

```sh
uv tool install "gha-downloader[mcp] @ git+https://github.com/astrojuanlu/gha-downloader.git"
```

### Invocation

```sh
gha-downloader-mcp
```

The server communicates over stdio using the MCP JSON-RPC protocol.
Use it with any MCP-compatible client (e.g. Claude Desktop, OpenCode).

### Tools

| Tool | Description |
|------|-------------|
| `get_run_info` | Fetch run metadata and job list without downloading. Pass `include_steps=True` for per-step detail with `step_label` values. Pass `only_failed=True` to return only non-successful jobs. |
| `list_artifacts` | List artifact names, sizes, and expiry status. Pass `job_id` to filter by job. Pass `only_available=False` to include expired artifacts. |
| `download_job` | Download logs and `run.json` for a single job. `job_id` is a required int. Cached on re-invocation; pass `force=True` to re-download. |
| `download_failed_jobs` | Download logs for only the failed/in-progress jobs. Useful for failure triage. |
| `download_artifact` | Download a single artifact by slug. Requires run directory to exist (call `download_job` first). Pass `force=True` to re-download. |
| `list_run_files` | Enumerate downloaded files for a run (logs + artifacts). |
| `list_logs` | List downloaded job slugs and their step labels. Does not return log content. |
| `list_artifact_files` | List files within a downloaded artifact directory. |
| `read_log_file` | Read log file content by job slug. Supports pagination (`offset`/`limit`), `tail` for last N lines, and `raw` to preserve ANSI codes. |
| `read_artifact_file` | Read text content of a file inside a downloaded artifact. Supports pagination and `raw` mode. |
| `search_log` | Search downloaded logs for lines matching a regex. Supports `job_slug`, `step_label`, `context_lines`, `max_results`. |

## Development

```sh
$ uv sync
$ uv run pytest
$ uv run ruff format && uv run ruff check --fix
$ uv run ty check
```
