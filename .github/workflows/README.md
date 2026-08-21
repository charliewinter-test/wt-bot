# .NET release → Slack alerter

Checks Microsoft's [release index](https://builds.dotnet.microsoft.com/dotnet/release-metadata/releases-index.json)
on a schedule and posts to Slack when a watched .NET channel gets a new release.
Security releases are flagged and enriched with their CVE list.

## Files

- `check_dotnet_releases.py` — the checker (Python stdlib only, no deps).
- `.github/workflows/dotnet-release-check.yml` — schedules it and commits state.
- `dotnet-release-state.json` — created automatically on first run; last-seen
  version per channel. Committing it back is how "only alert on new" works.

## Setup

1. **Create a Slack incoming webhook**
   Slack → your workspace app / *Incoming Webhooks* → add one for the target
   channel. Copy the `https://hooks.slack.com/services/...` URL.

2. **Add it as a repo secret**
   Repo → *Settings → Secrets and variables → Actions → New repository secret*
   Name it exactly `SLACK_WEBHOOK_URL`.

3. **Drop these files into the repo**, keeping the workflow at
   `.github/workflows/dotnet-release-check.yml`, and push.

4. **First run seeds silently.** Trigger it once from *Actions → .NET release
   check → Run workflow*. It records the current versions and sends nothing.
   Every run after that alerts only on genuinely new releases.

## Tuning (edit the `env:` block in the workflow)

- `WATCH_CHANNELS` — blank auto-watches every channel in **active** or
  **maintenance** support (skips preview and EOL). Pin specific ones with
  e.g. `"8.0,9.0,10.0"`.
- `SECURITY_ONLY` — set `"true"` to alert only on releases carrying CVE fixes.

## Notes

- Schedule is daily 08:00 UTC plus a Tuesday 18:30 UTC same-day pass. GitHub's
  scheduled runs can lag a few minutes under load; the daily pass guarantees you
  don't miss anything, including out-of-band security drops.
- If a Slack post fails, state is *not* saved, so the next run retries the alert
  rather than swallowing it.
- The workflow needs `contents: write` (already set) to commit the state file.
  If your org enforces read-only `GITHUB_TOKEN`, allow write for this repo under
  *Settings → Actions → General → Workflow permissions*.
