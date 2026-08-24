#!/usr/bin/env python3
"""Generate assets/github-panels.svg from public GitHub profile data."""

from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path


USERNAME = os.getenv("GITHUB_USERNAME", "jhonatan-gonzaga")
TOKEN = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")
OUT = Path(os.getenv("GITHUB_PANEL_OUTPUT", "assets/github-panels.svg"))


def request_json(url: str, token: str | None = None) -> dict | list:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "github-profile-readme-generator",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def graphql(query: str, variables: dict) -> dict:
    if not TOKEN:
        raise RuntimeError("GraphQL requires GH_TOKEN or GITHUB_TOKEN.")

    body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=body,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": "github-profile-readme-generator",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("errors"):
        raise RuntimeError(json.dumps(payload["errors"], ensure_ascii=False))
    return payload["data"]


def profile_from_graphql(username: str) -> dict:
    query = """
    query ProfilePanel($login: String!) {
      user(login: $login) {
        login
        name
        followers { totalCount }
        following { totalCount }
        repositories(ownerAffiliations: OWNER, privacy: PUBLIC) { totalCount }
        pinnedItems(first: 6, types: REPOSITORY) {
          nodes {
            ... on Repository {
              name
              url
              description
              stargazerCount
              forkCount
              updatedAt
              primaryLanguage { name color }
            }
          }
        }
        contributionsCollection {
          contributionCalendar {
            totalContributions
            weeks {
              contributionDays {
                date
                contributionCount
                color
              }
            }
          }
        }
      }
    }
    """
    data = graphql(query, {"login": username})["user"]
    repos = []
    for repo in data["pinnedItems"]["nodes"]:
        lang = repo.get("primaryLanguage") or {}
        repos.append(
            {
                "name": repo["name"],
                "description": repo.get("description") or "",
                "language": lang.get("name") or "",
                "color": lang.get("color") or "#6b7280",
                "stars": repo.get("stargazerCount", 0),
                "forks": repo.get("forkCount", 0),
                "url": repo.get("url") or "",
            }
        )

    calendar = data["contributionsCollection"]["contributionCalendar"]
    return {
        "name": data.get("name") or username,
        "login": data["login"],
        "public_repos": data["repositories"]["totalCount"],
        "followers": data["followers"]["totalCount"],
        "following": data["following"]["totalCount"],
        "total_contributions": calendar["totalContributions"],
        "weeks": calendar["weeks"],
        "repos": repos,
        "source": "graphql",
    }


def scrape_contribution_total(username: str) -> int:
    url = f"https://github.com/{username}"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "text/html",
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
            "User-Agent": "github-profile-readme-generator",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        body = response.read().decode("utf-8", "ignore")

    patterns = [
        r"([0-9][0-9.,]*)\s+contributions?\s+in\s+the\s+last\s+year",
        r"([0-9][0-9.,]*)\s+contribui\S+",
    ]
    for pattern in patterns:
        match = re.search(pattern, body, re.IGNORECASE)
        if match:
            return int(match.group(1).replace(".", "").replace(",", ""))
    return 0


def fallback_weeks(total: int) -> list[dict]:
    colors = ["#161b22", "#0e4429", "#006d32", "#26a641", "#39d353"]
    weeks = []
    remaining = max(total, 0)
    for week in range(53):
        days = []
        for day in range(7):
            wave = (week * 7 + day * 3 + len(USERNAME)) % 11
            count = 0
            if remaining > 0 and wave in {1, 4, 7, 9}:
                count = min((wave % 4) + 1, remaining)
                remaining -= count
            level = 0 if count == 0 else min(4, count)
            days.append(
                {
                    "date": "",
                    "contributionCount": count,
                    "color": colors[level],
                }
            )
        weeks.append({"contributionDays": days})
    return weeks


def profile_from_rest(username: str) -> dict:
    user = request_json(f"https://api.github.com/users/{username}", TOKEN)
    repos = request_json(
        f"https://api.github.com/users/{username}/repos?per_page=100&sort=updated",
        TOKEN,
    )

    cards = []
    for repo in repos:
        if repo.get("fork") or repo["name"].lower() == username.lower():
            continue
        cards.append(
            {
                "name": repo["name"],
                "description": repo.get("description") or "",
                "language": repo.get("language") or "",
                "color": language_color(repo.get("language") or ""),
                "stars": repo.get("stargazers_count", 0),
                "forks": repo.get("forks_count", 0),
                "url": repo.get("html_url") or "",
            }
        )
        if len(cards) == 6:
            break

    total = scrape_contribution_total(username)
    return {
        "name": user.get("name") or username,
        "login": user.get("login") or username,
        "public_repos": user.get("public_repos", len(repos)),
        "followers": user.get("followers", 0),
        "following": user.get("following", 0),
        "total_contributions": total,
        "weeks": fallback_weeks(total),
        "repos": cards,
        "source": "rest",
    }


def language_color(language: str) -> str:
    colors = {
        "C": "#555555",
        "HTML": "#e34c26",
        "JavaScript": "#f1e05a",
        "Python": "#3572a5",
        "TypeScript": "#3178c6",
    }
    return colors.get(language, "#6b7280")


def shorten(value: str, limit: int) -> str:
    value = " ".join((value or "").split())
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)].rstrip() + "..."


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def repo_rows(repos: list[dict]) -> str:
    if not repos:
        repos = [
            {
                "name": "Nenhum repositorio fixado",
                "description": "Aguardando dados do GitHub.",
                "language": "",
                "color": "#6b7280",
                "stars": 0,
                "forks": 0,
            }
        ]

    rows = []
    y = 156
    for index, repo in enumerate(repos[:4], start=1):
        desc = shorten(repo.get("description") or "Repositorio publico do perfil.", 52)
        language = repo.get("language") or "Public"
        rows.append(
            f"""
  <g class="repo-row" transform="translate(78 {y})">
    <rect width="500" height="54" rx="12" fill="#101827" stroke="#1f2937"/>
    <circle cx="22" cy="27" r="6" fill="{esc(repo.get("color") or "#6b7280")}"/>
    <text x="42" y="23" class="repo-name">{esc(repo["name"])}</text>
    <text x="42" y="43" class="repo-desc">{esc(desc)}</text>
    <text x="382" y="23" class="repo-lang">{esc(language)}</text>
    <text x="382" y="43" class="repo-meta">stars {esc(repo.get("stars", 0))} forks {esc(repo.get("forks", 0))}</text>
    <text x="470" y="33" class="repo-index">{index:02d}</text>
  </g>"""
        )
        y += 66
    return "\n".join(rows)


def heatmap(weeks: list[dict]) -> str:
    weeks = weeks[-53:] if weeks else fallback_weeks(0)
    cells = []
    start_x = 648
    start_y = 166
    size = 7
    gap = 2
    for week_index, week in enumerate(weeks):
        days = week.get("contributionDays", [])
        for day_index, day in enumerate(days[:7]):
            x = start_x + week_index * (size + gap)
            y = start_y + day_index * (size + gap)
            color = day.get("color") or "#161b22"
            count = int(day.get("contributionCount") or 0)
            opacity = "0.62" if count == 0 else "1"
            cells.append(
                f'<rect x="{x}" y="{y}" width="{size}" height="{size}" rx="2" fill="{esc(color)}" opacity="{opacity}"/>'
            )
    return "\n    ".join(cells)


def render_svg(profile: dict) -> str:
    now = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%M UTC")
    total = profile["total_contributions"]
    repos = profile["public_repos"]
    followers = profile["followers"]
    following = profile["following"]
    return f"""<svg width="1200" height="470" viewBox="0 0 1200 470" fill="none" xmlns="http://www.w3.org/2000/svg" role="img" aria-labelledby="title desc">
  <title id="title">Painel GitHub de {esc(profile["login"])}</title>
  <desc id="desc">Repositorios fixados e calendario de contribuicoes gerados automaticamente.</desc>
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1200" y2="470" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="#020617"/>
      <stop offset="0.48" stop-color="#0b1120"/>
      <stop offset="1" stop-color="#111827"/>
    </linearGradient>
    <linearGradient id="accent" x1="64" y1="66" x2="1136" y2="356" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="#22c55e"/>
      <stop offset="0.45" stop-color="#06b6d4"/>
      <stop offset="1" stop-color="#818cf8"/>
    </linearGradient>
    <radialGradient id="glowA" cx="0" cy="0" r="1" gradientUnits="userSpaceOnUse" gradientTransform="translate(220 64) rotate(62) scale(360 220)">
      <stop stop-color="#22c55e" stop-opacity="0.28"/>
      <stop offset="1" stop-color="#22c55e" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="glowB" cx="0" cy="0" r="1" gradientUnits="userSpaceOnUse" gradientTransform="translate(930 350) rotate(132) scale(420 250)">
      <stop stop-color="#38bdf8" stop-opacity="0.24"/>
      <stop offset="1" stop-color="#38bdf8" stop-opacity="0"/>
    </radialGradient>
    <pattern id="grid" width="42" height="42" patternUnits="userSpaceOnUse">
      <path d="M 42 0 H 0 V 42" stroke="#1f2937" stroke-width="1"/>
    </pattern>
    <filter id="shadow" x="-20%" y="-20%" width="140%" height="140%">
      <feDropShadow dx="0" dy="16" stdDeviation="16" flood-color="#020617" flood-opacity="0.45"/>
    </filter>
    <style>
      text {{
        font-family: "Fira Code", "JetBrains Mono", "Courier New", monospace;
      }}
      .panel {{
        fill: #0b1220;
        stroke: #1f2937;
      }}
      .eyebrow {{
        fill: #22c55e;
        font-size: 13px;
        letter-spacing: 1px;
      }}
      .title {{
        fill: #f8fafc;
        font-size: 28px;
        font-weight: 700;
      }}
      .muted {{
        fill: #94a3b8;
        font-size: 13px;
      }}
      .metric {{
        fill: #f8fafc;
        font-size: 34px;
        font-weight: 700;
      }}
      .metric-label {{
        fill: #94a3b8;
        font-size: 13px;
      }}
      .repo-name {{
        fill: #e5e7eb;
        font-size: 15px;
        font-weight: 700;
      }}
      .repo-desc, .repo-lang, .repo-meta {{
        fill: #94a3b8;
        font-size: 12px;
      }}
      .repo-index {{
        fill: #334155;
        font-size: 18px;
        font-weight: 700;
      }}
      .pulse {{
        animation: pulse 2.7s ease-in-out infinite;
      }}
      @keyframes pulse {{
        0%, 100% {{ opacity: 0.5; }}
        50% {{ opacity: 1; }}
      }}
    </style>
  </defs>

  <rect width="1200" height="470" rx="28" fill="url(#bg)"/>
  <rect width="1200" height="470" rx="28" fill="url(#grid)" opacity="0.48"/>
  <rect width="1200" height="470" rx="28" fill="url(#glowA)"/>
  <rect width="1200" height="470" rx="28" fill="url(#glowB)"/>
  <path d="M64 72H1136V410H64V72Z" stroke="url(#accent)" stroke-width="1.3" opacity="0.68"/>

  <text x="78" y="48" class="eyebrow">AUTO GENERATED GITHUB PANEL</text>
  <text x="364" y="48" class="muted">updated {esc(now)}</text>

  <g filter="url(#shadow)">
    <rect x="56" y="86" width="548" height="326" rx="22" class="panel"/>
    <rect x="628" y="86" width="516" height="326" rx="22" class="panel"/>
  </g>

  <text x="78" y="124" class="title">Pinned repositories</text>
  <text x="78" y="148" class="muted">Repositorios fixados ou recentes do perfil publico</text>
  {repo_rows(profile["repos"])}

  <text x="648" y="124" class="title">{esc(total)} contributions</text>
  <text x="648" y="148" class="muted">in the last year</text>
  <g>
    {heatmap(profile["weeks"])}
  </g>

  <g transform="translate(648 286)">
    <rect width="144" height="62" rx="15" fill="#101827" stroke="#1f2937"/>
    <text x="18" y="27" class="metric">{esc(repos)}</text>
    <text x="18" y="48" class="metric-label">public repos</text>
  </g>
  <g transform="translate(812 286)">
    <rect width="144" height="62" rx="15" fill="#101827" stroke="#1f2937"/>
    <text x="18" y="27" class="metric">{esc(followers)}</text>
    <text x="18" y="48" class="metric-label">followers</text>
  </g>
  <g transform="translate(976 286)">
    <rect width="144" height="62" rx="15" fill="#101827" stroke="#1f2937"/>
    <text x="18" y="27" class="metric">{esc(following)}</text>
    <text x="18" y="48" class="metric-label">following</text>
  </g>

  <rect x="78" y="438" width="1044" height="12" rx="6" fill="#111827"/>
  <rect x="78" y="438" width="672" height="12" rx="6" fill="#22c55e" opacity="0.82" class="pulse"/>
  <text x="770" y="449" class="muted">profile data rendered from GitHub</text>
</svg>
"""


def main() -> int:
    try:
        profile = profile_from_graphql(USERNAME)
    except Exception as exc:
        print(f"GraphQL unavailable, using REST fallback: {exc}", file=sys.stderr)
        profile = profile_from_rest(USERNAME)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render_svg(profile), encoding="utf-8")
    print(f"Wrote {OUT} for {profile['login']} using {profile['source']}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
