#!/usr/bin/env python3
"""
Check for new .NET releases and alert Slack via webhook.

Data source: Microsoft's machine-readable release index.
  https://builds.dotnet.microsoft.com/dotnet/release-metadata/releases-index.json

State (last-seen version per channel) is persisted to a JSON file that the
workflow commits back to the repo, so only *new* releases trigger an alert.

No third-party dependencies — standard library only.

Environment variables:
  SLACK_WEBHOOK_URL   (required) Incoming-webhook URL, from a repo secret.
  WATCH_CHANNELS      (optional) Comma-separated channels, e.g. "8.0,9.0".
                      Blank = auto: every channel currently in "active" or
                      "maintenance" support (i.e. supported, non-preview).
  SECURITY_ONLY       (optional) "true" to alert only on releases that carry
                      CVE fixes. Default "false" (alert on every new release).
  STATE_FILE          (optional) Path to the state file. Default
                      "dotnet-release-state.json".
"""

import json
import os
import sys
import urllib.error
import urllib.request

INDEX_URL = "https://builds.dotnet.microsoft.com/dotnet/release-metadata/releases-index.json"
STATE_FILE = os.environ.get("STATE_FILE", "dotnet-release-state.json")
SLACK_WEBHOOK = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
WATCH_CHANNELS = [c.strip() for c in os.environ.get("WATCH_CHANNELS", "").split(",") if c.strip()]
SECURITY_ONLY = os.environ.get("SECURITY_ONLY", "false").strip().lower() == "true"
# Support phases we auto-watch when WATCH_CHANNELS is blank.
AUTO_PHASES = {"active", "maintenance"}

USER_AGENT = "moneybox-dotnet-release-checker"


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


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


def get_cves(channel_meta_url, release_version):
    """Pull the CVE list for a specific release from the per-channel releases.json.

    Returns a list of {"id": ..., "url": ...} dicts, or [] if unavailable.
    """
    try:
        data = fetch_json(channel_meta_url)
    except (urllib.error.URLError, json.JSONDecodeError, ValueError) as exc:
        print(f"  Could not fetch CVE detail from {channel_meta_url}: {exc}", file=sys.stderr)
        return []
    for rel in data.get("releases", []):
        if rel.get("release-version") == release_version:
            out = []
            for cve in rel.get("cve-list", []) or []:
                out.append({"id": cve.get("cve-id", "?"), "url": cve.get("cve-url", "")})
            return out
    return []


def build_blocks(updates):
    """Build Slack Block Kit blocks for one or more release updates."""
    n = len(updates)
    header = f":package: *.NET release update* — {n} new release{'s' if n != 1 else ''}"
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": header}}]

    for u in updates:
        blocks.append({"type": "divider"})
        icon = ":rotating_light:" if u["security"] else ":arrow_up:"
        title = f"{icon} *.NET {u['channel']} — {u['release']}*"
        if u["security"]:
            title += "  `security`"

        details = (
            f"SDK: `{u['sdk']}`  ·  Runtime: `{u['runtime']}`\n"
            f"Released: {u['date']}  ·  Support: {u['phase']}  ·  Type: {u['type']}"
        )

        links = (
            f"<https://github.com/dotnet/core/blob/main/release-notes/"
            f"{u['channel']}/{u['release']}/{u['release']}.md|Release notes>"
            f"  ·  <https://dotnet.microsoft.com/download/dotnet/{u['channel']}|Downloads>"
        )

        text = f"{title}\n{details}"
        if u["security"]:
            if u["cves"]:
                cve_lines = ", ".join(
                    f"<{c['url']}|{c['id']}>" if c["url"] else c["id"] for c in u["cves"]
                )
                text += f"\n:lock: CVEs: {cve_lines}"
            else:
                text += "\n:lock: Security release (CVE details unavailable)"
        text += f"\n{links}"

        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": text}})

    return blocks


def main():
    if not SLACK_WEBHOOK:
        print("ERROR: SLACK_WEBHOOK_URL is not set.", file=sys.stderr)
        return 1

    try:
        index = fetch_json(INDEX_URL)
    except (urllib.error.URLError, json.JSONDecodeError, ValueError) as exc:
        print(f"ERROR: could not fetch release index: {exc}", file=sys.stderr)
        return 1

    channels = index.get("releases-index", [])
    state = load_state()
    first_run = not state

    updates = []
    new_state = {}

    for ch in channels:
        cv = ch.get("channel-version")
        phase = ch.get("support-phase", "")
        if WATCH_CHANNELS:
            if cv not in WATCH_CHANNELS:
                continue
        else:
            if phase not in AUTO_PHASES:
                continue

        latest = ch.get("latest-release")
        new_state[cv] = latest

        if first_run:
            continue  # seed silently, don't alert on everything the first time

        if state.get(cv) == latest:
            continue  # no change

        is_security = bool(ch.get("security", False))
        if SECURITY_ONLY and not is_security:
            # Still record the new version so we don't re-flag it later.
            continue

        cves = []
        if is_security and ch.get("releases.json"):
            cves = get_cves(ch["releases.json"], latest)

        updates.append({
            "channel": cv,
            "release": latest,
            "sdk": ch.get("latest-sdk", "?"),
            "runtime": ch.get("latest-runtime", "?"),
            "date": ch.get("latest-release-date", "?"),
            "phase": phase,
            "type": ch.get("release-type", "?").upper(),
            "security": is_security,
            "cves": cves,
        })

    # Preserve any previously-tracked channels that dropped out of our watch set
    # (e.g. went EOL) so we don't lose the record.
    for k, v in state.items():
        new_state.setdefault(k, v)

    if first_run:
        save_state(new_state)
        print(f"First run: seeded baseline for {len(new_state)} channel(s). No alert sent.")
        return 0

    if not updates:
        save_state(new_state)
        print("No new .NET releases.")
        return 0

    updates.sort(key=lambda u: (not u["security"], u["channel"]))  # security first
    blocks = build_blocks(updates)
    fallback = f"{len(updates)} new .NET release(s)"
    try:
        status = post_slack(blocks, fallback)
        print(f"Posted {len(updates)} update(s) to Slack (HTTP {status}).")
    except urllib.error.URLError as exc:
        print(f"ERROR: Slack post failed: {exc}", file=sys.stderr)
        return 1  # don't save state, so we retry next run

    save_state(new_state)
    for u in updates:
        print(f"  {'[SEC] ' if u['security'] else '      '}.NET {u['channel']} -> {u['release']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
