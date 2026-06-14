# gha-downloader

![Warning: Vibe Coded](https://img.shields.io/badge/%E2%9A%A0%EF%B8%8F_warning-vibe_coded-orange?style=flat)

Download logs and artifacts from GitHub Actions runs for offline inspection.

## Installation

```sh
uv tool install .
```

During development, prefix commands with `uv run` instead:

```sh
uv run gha-download ...
```

## Usage

```sh
$ gh auth login
$ gha-download https://github.com/canonical/mysql-operators/actions/runs/27357958065
$ ls runs/27357958065/
artifacts/  logs/  run.json
```

The `--repo` is inferred from the URL. You can also use a numeric run ID:

```sh
$ gha-download 27357958065 --repo canonical/mysql-operators
```

### Prerequisites

`gh auth login` must have been run with the `repo` read scope. Unauthenticated runs produce a `gh: HTTP 401` error.

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
  artifacts/
    <artifact-slug>/
      ...                           # extracted contents
    expired-artifact/
      .expired                      # marker for expired artifacts
```

Step numbers may skip (skipped steps produce no file).

### Flags

`-v` / `-vv` can appear anywhere in the invocation.

```
gha-download [-h] [--repo ORG/REPO] [--job-id JOB_ID]
             [--dir DIR] [--force] [-v] RUN_ID
```

| Flag       | Default  | Description                                          |
|------------|----------|------------------------------------------------------|
| `RUN_ID`   | required | Numeric workflow run ID or full Actions URL          |
| `--repo`   | auto     | Repository in `ORG/REPO` format (inferred from URL)  |
| `--job-id` | none     | Filter logs and artifacts by job ID                  |
| `--dir`    | `./runs` | Root directory for downloads                         |
| `--force`  | off      | Overwrite existing run directory                     |
| `-v`       | 0        | Verbosity: `-v` INFO, `-vv` DEBUG                   |

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

### Invocation

```sh
gha-mcp-server
```

The server communicates over stdio using the MCP JSON-RPC protocol. Use it with any MCP-compatible client (e.g. Claude Desktop, opencode).

### Tools

| Tool | Description |
|------|-------------|
| `get_run_info` | Fetch run metadata and job list without downloading |
| `list_artifacts` | List artifact names, sizes, and expiry status for a run |
| `download_run` | Download all logs and artifacts for a run to disk |
| `list_run_files` | Enumerate downloaded files for a run (logs + artifacts) |
| `read_log` | Return the text content of a downloaded log file |
| `read_artifact_file` | Return the text content of a file inside a downloaded artifact |

### Prerequisites

`gh auth login` must have been run beforehand with the `repo` read scope.

## Development

```sh
$ uv sync
$ uv run pytest
$ uv run ruff check --fix && uv run ruff format
$ uv run ty check src
```
