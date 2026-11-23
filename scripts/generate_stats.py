#!/usr/bin/env python3
"""Build the stats SVGs for the aryank09 profile README from public GitHub data.

Numbers are fetched at run time so the README does not hard-code figures that
go stale. The card shows public project, star, contribution, and follower counts.

Usage:
  GITHUB_TOKEN=... GITHUB_USERNAME=aryank09 python scripts/generate_stats.py

The contribution total is all-time: each calendar year from GitHub's
contributionYears (or, without a token, from the account's created_at year
through this year) is summed. Private contributions are included only when
the profile's "Include private contributions on my profile" setting is on.

Without a token, falls back to the public REST API and the public
contributions calendar HTML.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from xml.sax.saxutils import escape

USERNAME = os.environ.get("GITHUB_USERNAME", "aryank09")
TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
OUT_DIR = Path(os.environ.get("STATS_OUT_DIR", Path(__file__).resolve().parents[1] / "assets"))
UA = "aryank09-profile-readme-stats"

PALETTES = {
    "dark": {
        "bg": "#0A0A0A",
        "fg": "#EDE8E0",
        "muted": "#8A8680",
        "line": "#222221",
    },
    "light": {
        "bg": "#F4F1EC",
        "fg": "#161616",
        "muted": "#6A6762",
        "line": "#E2DDD6",
    },
}


def request(url: str, accept: str = "application/vnd.github+json") -> tuple[int, bytes, str]:
    headers = {"User-Agent": UA, "Accept": accept}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read(), resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as err:
        return err.code, err.read(), err.headers.get("Content-Type", "") if err.headers else ""


def graphql(query: str, variables: dict | None = None) -> dict:
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    headers = {
        "User-Agent": UA,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    req = urllib.request.Request("https://api.github.com/graphql", data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode())
    if payload.get("errors"):
        raise RuntimeError(payload["errors"])
    return payload["data"]


def fetch_user_rest() -> dict:
    status, raw, _ = request(f"https://api.github.com/users/{USERNAME}")
    if status != 200:
        raise RuntimeError(f"user API {status}: {raw[:200]!r}")
    return json.loads(raw.decode())


def fetch_repos_rest() -> list[dict]:
    repos: list[dict] = []
    page = 1
    while True:
        status, raw, _ = request(
            f"https://api.github.com/users/{USERNAME}/repos?per_page=100&page={page}&type=owner&sort=updated"
        )
        if status != 200:
            raise RuntimeError(f"repos API {status}: {raw[:200]!r}")
        batch = json.loads(raw.decode())
        if not batch:
            break
        repos.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return repos


def fetch_year_total_html(year: int) -> int:
    """Read the public heading 'N contributions in YYYY' for one calendar year."""
    status, raw, _ = request(
        f"https://github.com/users/{USERNAME}/contributions?from={year}-01-01&to={year}-12-31",
        accept="text/html",
    )
    if status != 200:
        raise RuntimeError(f"contributions page {status} for {year}")
    html = raw.decode("utf-8", "replace")
    match = re.search(rf"([\d,]+)\s+contributions\s+in\s+{year}\b", html)
    if not match:
        raise RuntimeError(f"could not parse contribution total for {year}")
    return int(match.group(1).replace(",", ""))


def fetch_rolling_year_html() -> int:
    """The previous metric: the heading 'N contributions in the last year'."""
    status, raw, _ = request(
        f"https://github.com/users/{USERNAME}/contributions",
        accept="text/html",
    )
    if status != 200:
        raise RuntimeError(f"contributions page {status}")
    html = raw.decode("utf-8", "replace")
    match = re.search(r"([\d,]+)\s+contributions\s+in the last year", html)
    if not match:
        raise RuntimeError("could not parse rolling-year contribution total")
    return int(match.group(1).replace(",", ""))


def fetch_all_time_html(created_at: str) -> int:
    start = int(created_at[:4])
    end = datetime.now(ZoneInfo("UTC")).year
    total = 0
    for year in range(start, end + 1):
        total += fetch_year_total_html(year)
    print(
        f"contributions: public calendar {start}-{end} summed to {total}",
        file=sys.stderr,
    )
    return total


def fetch_all_time_graphql() -> int:
    meta = graphql(
        """
        query ($login: String!) {
          user(login: $login) {
            contributionsCollection { contributionYears }
          }
        }
        """,
        {"login": USERNAME},
    )
    years = meta["user"]["contributionsCollection"]["contributionYears"]
    if not years:
        raise RuntimeError("no contribution years")
    fields = " ".join(
        f'y{year}: contributionsCollection(from: "{year}-01-01T00:00:00Z", to: "{year}-12-31T23:59:59Z") '
        "{ contributionCalendar { totalContributions } }"
        for year in years
    )
    data = graphql(
        f"query ($login: String!) {{ user(login: $login) {{ {fields} }} }}",
        {"login": USERNAME},
    )["user"]
    total = sum(data[f"y{year}"]["contributionCalendar"]["totalContributions"] for year in years)
    print(
        f"contributions: graphql all-time years {min(years)}-{max(years)} summed to {total}",
        file=sys.stderr,
    )
    return total


def collect() -> dict:
    user = fetch_user_rest()
    owned = [repo for repo in fetch_repos_rest() if not repo.get("fork") and not repo.get("private")]
    # The username/username profile repo is not a project; still count its stars if any.
    projects = [repo for repo in owned if repo.get("name") != USERNAME]
    stars = sum(int(repo.get("stargazers_count") or 0) for repo in owned)

    contribs = None
    if TOKEN:
        try:
            contribs = fetch_all_time_graphql()
        except Exception as exc:  # noqa: BLE001 - fall back to public HTML
            print(f"graphql contributions failed, using public calendar ({exc})", file=sys.stderr)
    if contribs is None:
        created_at = user.get("created_at") or "2023-01-01T00:00:00Z"
        contribs = fetch_all_time_html(created_at)

    return {
        "login": user.get("login") or USERNAME,
        "followers": int(user.get("followers") or 0),
        "public_repos": int(user.get("public_repos") or 0),
        "own_repos": len(projects),
        "stars": stars,
        "contributions": contribs,
        "generated_at": datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d"),
    }


def format_number(value: int) -> str:
    return f"{value:,}"


def card_svg(data: dict, palette: dict, mode: str) -> str:
    width, height = 1200, 204
    stats = [
        (format_number(data["own_repos"]), "Public projects"),
        (format_number(data["stars"]), "Stars"),
        (format_number(data["contributions"]), "Contributions, all time"),
        (format_number(data["followers"]), "Followers"),
    ]
    gap = 24
    col_w = (width - 80 - gap * 3) / 4
    parts: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="Public GitHub stats for {escape(data["login"])}">',
        f'<title>Public GitHub stats — {escape(data["login"])}</title>',
        "<style><![CDATA[",
        "  .num { font: 600 36px ui-sans-serif, system-ui, -apple-system, 'Segoe UI', sans-serif; letter-spacing: -0.03em; }",
        "  .lbl { font: 500 12px ui-sans-serif, system-ui, -apple-system, 'Segoe UI', sans-serif; letter-spacing: 0.12em; text-transform: uppercase; }",
        "  .kicker, .note { font: 500 12px ui-sans-serif, system-ui, -apple-system, 'Segoe UI', sans-serif; }",
        "  .kicker { letter-spacing: 0.16em; text-transform: uppercase; }",
        "  .reveal { opacity: 1; }",
        "  @keyframes rise { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: translateY(0); } }",
        "  .reveal { animation: rise 0.5s cubic-bezier(0.23, 1, 0.32, 1) forwards; }",
        "  @media (prefers-reduced-motion: reduce) { .reveal { animation: none; opacity: 1; transform: none; } }",
        "]]></style>",
        f'<rect width="{width}" height="{height}" fill="{palette["bg"]}"/>',
        f'<text class="kicker reveal" x="40" y="36" fill="{palette["muted"]}">Public signal</text>',
        f'<line x1="40" y1="52" x2="{width-40}" y2="52" stroke="{palette["line"]}" stroke-width="1"/>',
    ]
    for index, (number, label) in enumerate(stats):
        x = 40 + index * (col_w + gap)
        delay = 0.04 * index
        parts.append(
            f'<g class="reveal" style="animation-delay:{delay:.2f}s">'
            f'<text class="num" x="{x:.1f}" y="108" fill="{palette["fg"]}">{escape(number)}</text>'
            f'<text class="lbl" x="{x:.1f}" y="132" fill="{palette["muted"]}">{escape(label)}</text>'
            f"</g>"
        )
    parts.append(f'<line x1="40" y1="156" x2="{width-40}" y2="156" stroke="{palette["line"]}" stroke-width="1"/>')
    note = f"Own public repositories, excluding forks. Updated {data['generated_at']}."
    parts.append(
        f'<text class="note reveal" style="animation-delay:0.24s" x="40" y="184" fill="{palette["muted"]}">{escape(note)}</text>'
    )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    data = collect()
    (OUT_DIR / "stats.json").write_text(json.dumps(data, indent=2) + "\n")
    for mode, palette in PALETTES.items():
        path = OUT_DIR / f"stats-{mode}.svg"
        path.write_text(card_svg(data, palette, mode))
        print(f"wrote {path}")
    print(json.dumps({k: data[k] for k in ("own_repos", "stars", "contributions", "followers")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
