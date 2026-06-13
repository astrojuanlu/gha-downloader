# gha-downloader

![Warning: Vibe Coded](https://img.shields.io/badge/%E2%9A%A0%EF%B8%8F_warning-vibe_coded-orange?style=flat)

Download logs and artifacts from GitHub Actions runs for offline inspection.

## Usage

```
$ gh auth login
$ gha-downloader -v run download 27357958065
$ ls runs/27357958065/
artifacts/  logs/  run.json
```

During development, prefix with `uv run`:

The repository is auto-detected from the current directory's git remote.
Override with `--repo ORG/REPO`:

```
$ gha-downloader run download 27357958065 --repo canonical/mysql-operators
```

Filter by job ID:

```
$ gha-downloader run download 27357958065 --job-id 80847830020
```

### On-disk layout

```
runs/27357958065/
  run.json                          # workflow run metadata
  logs/
    <job-slug>/
      full.log                      # complete job log
      01_set-up-job.txt             # per-step logs from workflow YAML
      02_checkout.txt
      ...
  artifacts/
    <artifact-slug>/
      ...                           # extracted contents
```

Expired artifacts leave a `.expired` marker instead of contents.

### Flags

```
gha-downloader run download [-h] [--repo ORG/REPO] [--job-id JOB_ID]
                            [--dir DIR] [--force] RUN_ID
```

| Flag       | Default  | Description                              |
|------------|----------|------------------------------------------|
| `RUN_ID`   | required | Numeric workflow run ID                  |
| `--repo`   | auto     | Repository in `ORG/REPO` format          |
| `--job-id` | none     | Filter logs and artifacts by job ID      |
| `--dir`    | `./runs` | Root directory for downloads             |
| `--force`  | off      | Overwrite existing run directory         |
| `-v`       | 0        | Verbosity: `-v` INFO, `-vv` DEBUG        |

### Exit codes

| Code | Meaning          |
|------|------------------|
| 0    | Success          |
| 1    | Internal error   |
| 2    | User error       |
| 3    | Network error    |
| 130  | Interrupted      |

## Development

```
$ uv sync
$ uv run pytest
$ uv run ruff check --fix && uv run ruff format
$ uv run ty check src
```
