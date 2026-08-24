# wt-bot

A small suite of GitHub Actions workflows that watch for new upstream releases of
software deployed across the fleet and post an alert to Slack when a new version
ships. It closes the gap between a vendor cutting a release and us noticing — so
packaging, patching, and vulnerability remediation can start the same day.

No third-party dependencies: every checker is a single Python script using the
standard library only.

## What it watches

| Software | Source | Notifies |
| --- | --- | --- |
| .NET SDK / runtime | Microsoft [releases-index.json](https://builds.dotnet.microsoft.com/dotnet/release-metadata/releases-index.json) | Slack |
| GitHub Copilot (desktop app) | GitHub Releases of `github/app` | Slack |
| Azure Storage Explorer | GitHub Releases of `microsoft/AzureStorageExplorer` | Slack |

The .NET checker additionally flags security releases and enriches the alert with
the CVE list for that release.

## How it works

Each watcher is a scheduled workflow that:

1. Fetches the latest published version(s) from the vendor's release source.
2. Compares against the last-seen version held in a per-tool state file.
3. Posts a formatted message to Slack via an incoming webhook if the version is new.
4. Writes the new version back to the state file, which the workflow commits to the
   repo — that committed state is how "only alert on new" works.

If a Slack post fails, the state is **not** saved, so the next run retries the alert
rather than swallowing it.

## Repository layout

```text
.github/workflows/
  dotnet-release-check.yml            # .NET — daily 08:00 UTC + Tue 18:30 UTC
  copilot-release-check.yml           # Copilot — daily 08:05 UTC
  storage-explorer-release-check.yml  # Storage Explorer — daily 08:10 UTC
  README.md                           # .NET-specific setup notes

check_dotnet_releases.py
check_copilot_releases.py
check_storage_explorer_releases.py

dotnet-release-state.json             # last-seen version per .NET channel
copilot-release-state.json            # last-seen Copilot version
storage-explorer-release-state.json   # last-seen Storage Explorer version
```

State files are created/updated automatically; they're committed by the workflows,
not edited by hand.

## Setup

### 1. Create Slack incoming webhooks

Each watcher posts through its own webhook so alerts arrive from a distinct Slack
app. Create a webhook per channel you want (they can point at the same channel if
you prefer), then copy each `https://hooks.slack.com/services/...` URL.

### 2. Add them as repository secrets

Under **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Used by | Required |
| --- | --- | --- |
| `SLACK_WEBHOOK_URL` | .NET check | Yes |
| `COPILOT_SLACK_WEBHOOK_URL` | Copilot check | Yes |
| `STORAGE_EXPLORER_SLACK_WEBHOOK_URL` | Storage Explorer check | Yes |
| `GITHUB_TOKEN` | Copilot + Storage Explorer checks | Built-in — no action needed |

`GITHUB_TOKEN` is provided automatically by Actions and is used only to lift the
GitHub API rate limit when reading releases.

### 3. Allow the workflows to commit state

Each workflow needs `contents: write` (already set in the YAML) to push the updated
state file. If your org enforces a read-only default token, enable write for this
repo under **Settings → Actions → General → Workflow permissions**.

### 4. Seed silently on first run

Trigger each workflow once from **Actions → (workflow) → Run workflow**. The first
run records current versions and sends nothing; every run after that alerts only on
genuinely new releases.

## Running a check on demand

Every workflow supports `workflow_dispatch`:

**GitHub UI:** Actions → select the workflow → **Run workflow**.

**GitHub CLI:**

```bash
gh workflow run dotnet-release-check.yml
gh workflow run copilot-release-check.yml
gh workflow run storage-explorer-release-check.yml
```

## Tuning

Edit the `env:` block of the relevant workflow.

**.NET (`dotnet-release-check.yml`)**

- `WATCH_CHANNELS` — blank auto-watches every channel currently in **active** or
  **maintenance** support (skips preview and EOL). Pin specific channels with
  e.g. `"8.0,9.0,10.0"`.
- `SECURITY_ONLY` — `"true"` to alert only on releases that carry CVE fixes.

**Copilot / Storage Explorer**

- `REPO` — the GitHub repo to watch (`github/app` and
  `microsoft/AzureStorageExplorer` respectively).
- `INCLUDE_PRERELEASE` — `"true"` to also alert on prereleases/betas.

All three checkers also honour an optional `STATE_FILE` env var to override the
default state file path.

## Adding a new watcher

1. Copy one of the `check_*.py` scripts and adapt its release source and parsing.
2. Copy the matching workflow in `.github/workflows/`, renaming it and pointing its
   `run:` step at the new script and its `SLACK_WEBHOOK_URL` at a new secret.
3. Give it its own state file name and `concurrency` group.
4. Commit and push, then seed it with one manual run.

## Notes

- Scheduled runs can lag a few minutes under GitHub load. The .NET checker has a
  second Tuesday-evening pass to catch Patch Tuesday security drops promptly; the
  daily run remains the safety net.
- Secrets are never printed to logs — webhook URLs are referenced only via
  `${{ secrets.* }}`.
- If a watcher goes quiet, check the workflow run history first: a failed fetch
  (vendor endpoint change, rate limit) is a more common cause than "no new release".
