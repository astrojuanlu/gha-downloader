# gha-downloader

![Warning: Vibe Coded](https://img.shields.io/badge/%E2%9A%A0%EF%B8%8F_warning-vibe_coded-orange?style=flat)

Download logs and artifacts from GitHub Actions runs for offline inspection.

## Installation

The `gh` CLI must be installed and proper `gh auth` must be available with the `repo` read scope.

```sh
uv tool install "gha-downloader @ git+https://github.com/astrojuanlu/gha-downloader.git"
```

## Usage

```sh
$ gh auth login
$ gha-download https://github.com/canonical/mysql-operators/actions/runs/27357958065
$ ls runs/27357958065/
logs/  run.json
```

The `--repo` is inferred from the URL. You can also use a numeric run ID:

```sh
$ gha-download 27357958065 --repo canonical/mysql-operators
```

### Repo inference

There are two repo-inference paths:

1. **URL-path extraction** — when you pass a full GitHub Actions URL, `ORG/REPO` is extracted from the `github.com/ORG/REPO/actions/` segment. This works regardless of your current working directory.
2. **git-remote detection** — when you pass a numeric run ID without `--repo`, the CLI auto-detects the repo from the `.git` remote. This only works when running from inside a clone of the target repository.

For cross-repo downloads, pass `--repo ORG/REPO` explicitly.

### URL job-ID filter

A URL containing `/job/ID` downloads only that one job, acting as an implicit `--job-id` filter:

```sh
$ gha-download https://github.com/org/repo/actions/runs/27357958065/job/80847830020
```

If you also pass `--job-id` explicitly and the values differ, a warning is printed and `--job-id` takes precedence:

```
Warning: --job-id 999 overrides job ID 80847830020 from URL.
```

URL query parameters (e.g. `?pr=354`) are stripped and have no effect.

Filter by job ID manually:

```sh
$ gha-download 27357958065 --job-id 80847830020
```

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

### Artifact commands

List available artifacts for a run:

```sh
$ gha-download 27357958065 --list-artifacts
test-results  2.4 MB  available  slug: test-results
build-logs    0.5 MB  available  slug: build-logs
old-data      1.1 MB  expired    slug: old-data
```

Download a specific artifact (run directory created if absent):

```sh
$ gha-download 27357958065 --artifact test-results
$ ls runs/27357958065/artifacts/test-results/
...
```

Download multiple artifacts in one invocation:

```sh
$ gha-download 27357958065 --artifact test-results --artifact build-logs
```

### Flags

```
gha-download [-h] [--repo ORG/REPO] [--job-id JOB_ID]
             [--dir DIR] [--force] [-v]
             [--list-artifacts | --artifact NAME [...]]
             RUN_ID
```

| Flag                | Default  | Description                                          |
|---------------------|----------|------------------------------------------------------|
| `RUN_ID`            | required | Numeric workflow run ID or full Actions URL          |
| `--repo`            | auto     | Repository in `ORG/REPO` format (inferred from URL)  |
| `--job-id`          | none     | Filter logs and artifact listing by job ID          |
| `--dir`             | `./runs` | Root directory for downloads                         |
| `--force`           | off      | Overwrite existing run directory                     |
| `-v`                | 0        | Verbosity: `-v` INFO, `-vv` DEBUG                    |
| `--list-artifacts`  | off      | List artifacts (name, size, status, slug) and exit   |
| `--artifact NAME`   | none     | Download a named artifact. Repeatable. Mutually exclusive with `--list-artifacts`. |

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
| `get_run_info` | Fetch run metadata and job list without downloading. Pass `include_steps=True` for per-step detail with `step_label` values. Pass `only_failed=True` to return only non-successful jobs (useful for large matrix runs). |
| `list_artifacts` | List artifact names, sizes, and expiry status for a run. Pass `job_id` to list only artifacts uploaded by a specific job. |
| `download_run` | Download logs and `run.json` for a run to disk. `job_id` is required — pass a specific ID or `None` for all jobs. Cached on re-invocation; pass `force=True` to re-download. Returns a note on large runs (>20 jobs) when `job_id=None`. |
| `list_run_files` | Enumerate downloaded files for a run (logs + artifacts) |
| `list_logs` | List downloaded job slugs and their step labels. Use before `search_log` or `read_log_file` to discover available `job_slug` values. Does not return log content. |
| `search_log` | Search downloaded logs for lines matching a regex. Pass `job_slug` to scope to one job, `step_label` to scope to one step, `context_lines` for surrounding context. |
| `read_log_file` | Read content of a downloaded log file by job slug and optional step label. Supports pagination with `offset` and `limit`. |
| `read_artifact_file` | Return the text content of a file inside a downloaded artifact. ANSI escape codes are stripped by default; pass `raw=True` to preserve them. |

## Development

```sh
$ uv sync
$ uv run pytest
$ uv run ruff format && uv run ruff check --fix
$ uv run ty check
```
