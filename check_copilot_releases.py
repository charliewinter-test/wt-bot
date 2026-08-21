#!/usr/bin/env python3
"""
Check for new GitHub Copilot app releases and alert Slack via webhook.

Data source: GitHub Releases API for the Copilot app repo (github/app).
  https://api.github.com/repos/github/app/releases/latest

Same shape as the .NET checker: state (last-seen release tag) is persisted to a
JSON file that the workflow commits back, so only *new* releases trigger alerts.

No third-party dependencies — standard library only.

Environment variables:
  SLACK_WEBHOOK_URL   (required) Incoming-webhook URL, from a repo secret.
  GITHUB_TOKEN        (optional but recommended) Lifts the API rate limit from
                      60/hr to 1000/hr. The workflow supplies this automatically.
  REPO                (optional) owner/name to watch. Default "github/app"
                      (the standalone GitHub Copilot desktop app). Change this
                      if you want a different artifact, e.g. "github/CopilotForXcode".
  INCLUDE_PRERELEASE  (optional) "true" to also alert on prereleases/betas.
                      Default "false" (stable releases only).
  STATE_FILE          (optional) Default "copilot-release-state.json".
"""

import json
import os
import sys
import urllib.error
import urllib.request

REPO = os.environ.get("REPO", "github/app").strip()
STATE_FILE = os.environ.get("STATE_FILE", "copilot-release-state.json")
SLACK_WEBHOOK = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
INCLUDE_PRERELEASE = os.environ.get("INCLUDE_PRERELEASE", "false").strip().lower() == "true"

API_BASE = f"https://api.github.com/repos/{REPO}/releases"
USER_AGENT = "moneybox-copilot-release-checker"
BODY_PREVIEW_CHARS = 600


def gh_headers():
    h = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT}
    if GITHUB_TOKEN:
        h["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return h


def fetch_json(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def get_latest_release():
    """Return the release dict to consider, honouring INCLUDE_PRERELEASE.

    Uses /releases/latest for stable-only (its whole job is to skip
    prereleases and drafts). Falls back to the full list if that 404s
    (repo with only prereleases) or when prereleases are wanted.
    """
    if not INCLUDE_PRERELEASE:
        try:
            return fetch_json(f"{API_BASE}/latest", gh_headers())
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            # No stable release yet — fall through to the list.
    releases = fetch_json(f"{API_BASE}?per_page=20", gh_headers())
    for rel in releases:
        if rel.get("draft"):
            continue
        if rel.get("prerelease") and not INCLUDE_PRERELEASE:
            continue
        return rel
    return None


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        print(f"WARNING: {STATE_FILE} is not valid JSON; treating as empty.", file=sys.stderr)
        return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)
        f.write("\n")


def post_slack(blocks, fallback_text):
    payload = json.dumps({"text": fallback_text, "blocks": blocks}).encode()
    req = urllib.request.Request(
        SLACK_WEBHOOK,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status


def build_blocks(rel):
    tag = rel.get("tag_name", "?")
    name = rel.get("name") or tag
    date = (rel.get("published_at") or "")[:10]
    url = rel.get("html_url", f"https://github.com/{REPO}/releases")
    is_pre = rel.get("prerelease", False)

    icon = ":test_tube:" if is_pre else ":sparkles:"
    title = f"{icon} *GitHub Copilot app — {tag}*"
    if is_pre:
        title += "  `prerelease`"
    if name and name != tag:
        title += f"\n{name}"

    header = ":robot_face: *New GitHub Copilot app release*"
    text = f"{title}\nReleased: {date}  ·  Repo: `{REPO}`"

    # Short changelog preview + link to the full notes.
    body = (rel.get("body") or "").strip()
    if body:
        preview = body[:BODY_PREVIEW_CHARS]
        if len(body) > BODY_PREVIEW_CHARS:
            preview = preview.rstrip() + "…"
        text += f"\n\n{preview}"

    # macOS + Windows download assets, if present (handy for fleet packaging).
    assets = rel.get("assets", []) or []
    dl = []
    for a in assets:
        n = a.get("name", "")
        low = n.lower()
        if any(k in low for k in (".dmg", ".pkg", "mac", "darwin", "osx",
                                  ".msi", ".exe", "win", ".appimage", ".deb")):
            dl.append(f"<{a.get('browser_download_url','')}|{n}>")
    links = f"<{url}|Full release notes>"
    if dl:
        links += "  ·  " + "  ·  ".join(dl[:6])
    text += f"\n{links}"

    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": header}},
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
    ]


def main():
    if not SLACK_WEBHOOK:
        print("ERROR: SLACK_WEBHOOK_URL is not set.", file=sys.stderr)
        return 1

    try:
        rel = get_latest_release()
    except (urllib.error.URLError, json.JSONDecodeError, ValueError) as exc:
        print(f"ERROR: could not fetch releases for {REPO}: {exc}", file=sys.stderr)
        return 1

    if not rel:
        print(f"No matching release found for {REPO}.")
        return 0

    tag = rel.get("tag_name")
    state = load_state()
    first_run = REPO not in state

    if first_run:
        save_state({**state, REPO: tag})
        print(f"First run: seeded baseline {REPO} -> {tag}. No alert sent.")
        return 0

    if state.get(REPO) == tag:
        print(f"No new release. {REPO} still at {tag}.")
        return 0

    blocks = build_blocks(rel)
    fallback = f"New GitHub Copilot app release: {tag}"
    try:
        status = post_slack(blocks, fallback)
        print(f"Posted {REPO} {tag} to Slack (HTTP {status}).")
    except urllib.error.URLError as exc:
        print(f"ERROR: Slack post failed: {exc}", file=sys.stderr)
        return 1  # don't save state, so we retry next run

    save_state({**state, REPO: tag})
    return 0


if __name__ == "__main__":
    sys.exit(main())
