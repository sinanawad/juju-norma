#!/usr/bin/env python3
"""Deliver the nightly calibration verdict as ONE reused GitHub issue.

For a calibration charm the per-channel pass/fail record IS the deliverable, but
producing it is not the same as delivering it: the nightly matrix ran red for 42
consecutive nights (2026-06-30 -> 2026-08-10) with nobody told, and both causes
were upstream-fix signals the sentinels raised on purpose.

Design (deliberately quiet):
  * ONE issue per red streak, found by label. Never one issue per failed run.
  * While the failure set is unchanged the issue BODY is edited in place — no new
    comment, so a six-week streak stays a single readable artifact.
  * A comment is posted only when the failure set actually CHANGES (a test starts
    or stops failing), because that is the only new information.
  * All-green closes the issue.

Failures are split under two headings because they demand opposite responses:
  * regressions        -> we broke something; investigate.
  * strict-XPASS flips -> Juju SHIPPED A FIX for a bug this charm tracks; the
                          marker should be retired. That is good news, and must
                          not read as an outage.

State lives in a machine-readable comment embedded in the issue body, so no
external store is needed.

Usage:
    python3 scripts/nightly_verdict.py <artifacts-dir>

Env: GH_TOKEN, GITHUB_REPOSITORY, GITHUB_RUN_ID, GITHUB_SERVER_URL, GITHUB_API_URL
"""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

LABEL = "nightly-red"
STATE_RE = re.compile(r"<!-- norma-verdict: (?P<json>\{.*?\}) -->", re.S)
# pytest writes the strict-XPASS reason into the <failure message="...">.
XPASS_MARK = "[XPASS(strict)]"

API = os.environ.get("GITHUB_API_URL", "https://api.github.com")
REPO = os.environ.get("GITHUB_REPOSITORY", "")
RUN_ID = os.environ.get("GITHUB_RUN_ID", "")
SERVER = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
RUN_URL = f"{SERVER}/{REPO}/actions/runs/{RUN_ID}" if REPO and RUN_ID else ""


# ----------------------------------------------------------------- GitHub API


def _req(method: str, path: str, payload: dict | None = None):
    """Minimal GitHub REST call. Returns parsed JSON (or None for 204)."""
    token = os.environ["GH_TOKEN"]
    url = path if path.startswith("http") else f"{API}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read()
            return json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:400]
        raise RuntimeError(f"{method} {url} -> {exc.code}: {detail}") from exc


def ensure_label() -> None:
    """Create the tracking label if absent (422 = already exists)."""
    try:
        _req(
            "POST",
            f"/repos/{REPO}/labels",
            {"name": LABEL, "color": "b60205", "description": "Nightly calibration matrix is red"},
        )
    except RuntimeError as exc:
        if "422" not in str(exc):
            raise


def find_open_issue() -> dict | None:
    issues = _req("GET", f"/repos/{REPO}/issues?state=open&labels={LABEL}&per_page=1")
    return issues[0] if issues else None


# ------------------------------------------------------------------- parsing


def parse_reports(root: pathlib.Path) -> tuple[dict[str, dict[str, list[str]]], list[str]]:
    """Return ({channel: {"regressions": [...], "xpass": [...]}}, [problems])."""
    results: dict[str, dict[str, list[str]]] = {}
    problems: list[str] = []

    report_files = sorted(root.glob("*/report.xml"))
    if not report_files:
        problems.append(
            "No `report.xml` artifact was produced by any leg — the matrix did not run to the "
            "point of writing a report. Treat this as a failure, not a pass."
        )

    for path in report_files:
        # artifact dir name: test-report-<channel-with-dashes>-<mode>
        channel = path.parent.name.replace("test-report-", "")
        regressions: list[str] = []
        xpass: list[str] = []
        try:
            root_el = ET.parse(path).getroot()
        except ET.ParseError as exc:
            problems.append(f"`{channel}`: report.xml is unparseable ({exc}).")
            continue
        for case in root_el.iter("testcase"):
            for kind in ("failure", "error"):
                for el in case.findall(kind):
                    name = f"{case.get('classname', '')}::{case.get('name', '')}"
                    message = (el.get("message") or "") + (el.text or "")
                    (xpass if XPASS_MARK in message else regressions).append(name)
        if regressions or xpass:
            results[channel] = {
                "regressions": sorted(set(regressions)),
                "xpass": sorted(set(xpass)),
            }
    return results, problems


def signature(results: dict, problems: list[str]) -> list[str]:
    """Stable identity of the current failure set — drives comment-or-not."""
    sig = [f"!{p[:60]}" for p in problems]
    for channel in sorted(results):
        for kind in ("regressions", "xpass"):
            sig += [f"{channel}|{kind}|{t}" for t in results[channel][kind]]
    return sorted(sig)


# ------------------------------------------------------------------ rendering


def render(results: dict, problems: list[str], state: dict) -> str:
    first = state["first_seen"]
    streak = state["consecutive"]
    day_word = "night" if streak == 1 else "nights"
    out = [
        f"The nightly calibration matrix has been failing since **{first}** "
        f"({streak} consecutive {day_word}).",
        "",
    ]
    if RUN_URL:
        out += [f"Latest run: {RUN_URL}", ""]

    if problems:
        out += ["## ⚠️ Report integrity", ""]
        out += [f"- {p}" for p in problems]
        out += [""]

    xpass_any = {c: r["xpass"] for c, r in results.items() if r["xpass"]}
    regress_any = {c: r["regressions"] for c, r in results.items() if r["regressions"]}

    if xpass_any:
        out += [
            "## ✅ Upstream shipped a fix — retire this marker",
            "",
            "These are **strict-XPASS flips**, not regressions. A sentinel this charm planted for "
            "an upstream Juju bug started passing, which means Juju fixed it. Action: confirm the "
            "fix and its version floor, then narrow or remove the marker.",
            "",
        ]
        for channel in sorted(xpass_any):
            out.append(f"**{channel}**")
            out += [f"- `{t}`" for t in xpass_any[channel]]
            out.append("")

    if regress_any:
        out += [
            "## ❌ Regressions",
            "",
            "These failed for reasons other than a sentinel flip. Investigate.",
            "",
        ]
        for channel in sorted(regress_any):
            out.append(f"**{channel}**")
            out += [f"- `{t}`" for t in regress_any[channel]]
            out.append("")

    if state.get("stale_schedule"):
        out += [
            "## ⏰ Schedule liveness",
            "",
            state["stale_schedule"],
            "",
        ]

    out += [
        "---",
        "",
        "<sub>Opened and maintained by the `nightly-verdict` job. This issue is **reused** for "
        "the whole streak: the body is rewritten each night, and a comment is added only when "
        "the set of failing tests changes. It closes itself when the matrix goes green.</sub>",
        "",
        f"<!-- norma-verdict: {json.dumps(state, sort_keys=True)} -->",
    ]
    return "\n".join(line for line in out if line is not None)


def delta_comment(old: list[str], new: list[str]) -> str:
    started = [s for s in new if s not in old]
    cleared = [s for s in old if s not in new]
    lines = ["The set of failing tests changed."]
    if started:
        lines += ["", "**Newly failing**"] + [f"- `{s}`" for s in started]
    if cleared:
        lines += ["", "**No longer failing**"] + [f"- `{s}`" for s in cleared]
    if RUN_URL:
        lines += ["", f"Run: {RUN_URL}"]
    return "\n".join(lines)


# ------------------------------------------------------------------ liveness


def schedule_liveness() -> str:
    """Warn if the previous scheduled run is suspiciously old.

    Catches GitHub dropping scheduled runs under load. It cannot catch the
    schedule being disabled outright (60 days of repo inactivity) — nothing
    running inside the schedule can observe its own absence; that residual gap is
    documented in the plan rather than papered over.
    """
    try:
        runs = _req(
            "GET",
            f"/repos/{REPO}/actions/runs?event=schedule&status=completed&per_page=2",
        )
    except RuntimeError:
        return ""
    items = (runs or {}).get("workflow_runs", [])
    previous = [r for r in items if str(r.get("id")) != RUN_ID]
    if not previous:
        return ""
    when = dt.datetime.fromisoformat(previous[0]["created_at"].replace("Z", "+00:00"))
    # dt.UTC (ruff UP017) is 3.11+; this repo targets py310, so keep timezone.utc.
    hours = (dt.datetime.now(dt.timezone.utc) - when).total_seconds() / 3600  # noqa: UP017
    if hours > 48:
        return (
            f"The previous completed scheduled run was **{hours:.0f}h ago** "
            f"({previous[0]['created_at']}). Nightly runs are being skipped or dropped — "
            "GitHub also disables `schedule:` triggers after 60 days of repository inactivity."
        )
    return ""


# ---------------------------------------------------------------------- main


def main() -> int:
    if not REPO or "GH_TOKEN" not in os.environ:
        print("GH_TOKEN/GITHUB_REPOSITORY not set", file=sys.stderr)
        return 2

    artifacts = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "artifacts")
    results, problems = parse_reports(artifacts)
    failing = bool(results) or bool(problems)
    issue = find_open_issue()
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()  # noqa: UP017

    if not failing:
        if issue:
            _req(
                "POST",
                f"/repos/{REPO}/issues/{issue['number']}/comments",
                {"body": f"✅ Nightly matrix is green again — closing.\n\nRun: {RUN_URL}"},
            )
            _req("PATCH", f"/repos/{REPO}/issues/{issue['number']}", {"state": "closed"})
            print(f"green: closed #{issue['number']}")
        else:
            print("green: nothing to do")
        return 0

    sig = signature(results, problems)
    stale = schedule_liveness()

    if issue is None:
        ensure_label()
        state = {
            "first_seen": today,
            "consecutive": 1,
            "signature": sig,
            "stale_schedule": stale,
        }
        created = _req(
            "POST",
            f"/repos/{REPO}/issues",
            {
                "title": "Nightly calibration matrix is red",
                "body": render(results, problems, state),
                "labels": [LABEL],
            },
        )
        print(f"opened #{created['number']}")
        return 0

    prev = {}
    match = STATE_RE.search(issue.get("body") or "")
    if match:
        try:
            prev = json.loads(match.group("json"))
        except json.JSONDecodeError:
            prev = {}
    old_sig = prev.get("signature", [])
    state = {
        "first_seen": prev.get("first_seen", today),
        "consecutive": int(prev.get("consecutive", 0)) + 1,
        "signature": sig,
        "stale_schedule": stale,
    }

    # Body is rewritten every night; a comment is posted ONLY on a real change.
    _req(
        "PATCH",
        f"/repos/{REPO}/issues/{issue['number']}",
        {"body": render(results, problems, state)},
    )
    if sig != old_sig:
        _req(
            "POST",
            f"/repos/{REPO}/issues/{issue['number']}/comments",
            {"body": delta_comment(old_sig, sig)},
        )
        print(f"updated #{issue['number']} (+comment: failure set changed)")
    else:
        print(f"updated #{issue['number']} (no change, no comment)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
