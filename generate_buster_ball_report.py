#!/usr/bin/env python3
"""Generate and save a daily Buster Ball Giants scouting report."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from openai import OpenAI


GIANTS_TEAM_ID = 137
PACIFIC = ZoneInfo("America/Los_Angeles")
SEASON_END = date(2026, 9, 27)
MAX_REPORT_CHARACTERS = 6600
MLB_API = "https://statsapi.mlb.com/api/v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="Report date in YYYY-MM-DD format (Pacific time)")
    parser.add_argument(
        "--model", default=os.getenv("REPORT_MODEL", "gpt-6-astra"),
        help="OpenAI model (default: REPORT_MODEL or gpt-6-astra)",
    )
    parser.add_argument("--overwrite", action="store_true", help="Replace today's report")
    return parser.parse_args()


def fetch_json(path: str, **params: object) -> dict:
    query = urllib.parse.urlencode(params)
    url = f"{MLB_API}/{path}?{query}" if query else f"{MLB_API}/{path}"
    request = urllib.request.Request(url, headers={"User-Agent": "BusterBall-Podcast/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def team_abbreviation(team_id: int) -> str:
    payload = fetch_json(f"teams/{team_id}")
    team = payload.get("teams", [{}])[0]
    return team.get("abbreviation") or team.get("teamCode", "MLB").upper()


def schedule_games(start: date, end: date) -> list[dict]:
    payload = fetch_json(
        "schedule",
        sportId=1,
        teamId=GIANTS_TEAM_ID,
        startDate=start.isoformat(),
        endDate=end.isoformat(),
        hydrate="probablePitcher,venue,team",
    )
    return [game for day in payload.get("dates", []) for game in day.get("games", [])]


def game_summary(game: dict) -> dict:
    teams = game.get("teams", {})
    away = teams.get("away", {})
    home = teams.get("home", {})
    return {
        "official_date": game.get("officialDate"),
        "game_time_utc": game.get("gameDate"),
        "status": game.get("status", {}).get("detailedState"),
        "venue": game.get("venue", {}).get("name"),
        "away_team": away.get("team", {}).get("name"),
        "away_record": away.get("leagueRecord"),
        "away_probable_pitcher": away.get("probablePitcher", {}).get("fullName"),
        "home_team": home.get("team", {}).get("name"),
        "home_record": home.get("leagueRecord"),
        "home_probable_pitcher": home.get("probablePitcher", {}).get("fullName"),
    }


def report_filename(target: date, todays_games: list[dict]) -> str:
    if len(todays_games) > 1:
        return f"Buster_Ball_SFG_Doubleheader_{target.isoformat()}.md"
    if not todays_games:
        return f"Buster_Ball_SFG_Off_Day_{target.isoformat()}.md"
    teams = todays_games[0]["teams"]
    away_id = int(teams["away"]["team"]["id"])
    home_id = int(teams["home"]["team"]["id"])
    return (
        f"Buster_Ball_{team_abbreviation(away_id)}_at_"
        f"{team_abbreviation(home_id)}_{target.isoformat()}.md"
    )


def clean_markdown(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:markdown)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    text = re.sub(r"\s*cite[^]+", "", text)
    return text.strip() + "\n"


def shorten_report(client: OpenAI, model: str, report: str) -> str:
    response = client.responses.create(
        model=model,
        input=(
            "Edit the following Buster Ball Podcast script to 6,400 characters or fewer. "
            "Preserve the verified facts, sabermetric analysis, tactical keys, prediction, "
            "Billy B solo-host voice, Markdown headings, and final sign-off. Return only Markdown.\n\n"
            + report
        ),
    )
    return clean_markdown(response.output_text)


def enforce_limit(report: str) -> str:
    if len(report) <= MAX_REPORT_CHARACTERS:
        return report
    ending = "\n\nThat’s today’s Buster Ball Podcast scouting report. I’m Billy B.\n"
    room = MAX_REPORT_CHARACTERS - len(ending)
    shortened = report[:room]
    boundary = max(shortened.rfind("\n\n"), shortened.rfind(". "))
    if boundary > room - 800:
        shortened = shortened[: boundary + 1].rstrip()
    return shortened + ending


def write_github_output(name: str, value: str) -> None:
    output = os.getenv("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as stream:
            stream.write(f"{name}={value}\n")


def main() -> int:
    args = parse_args()
    target = (
        datetime.strptime(args.date, "%Y-%m-%d").date()
        if args.date
        else datetime.now(PACIFIC).date()
    )

    if not args.date and target > SEASON_END:
        print(f"Regular-season automation ended on {SEASON_END.isoformat()}; nothing created.")
        write_github_output("created", "false")
        return 0

    games = schedule_games(target - timedelta(days=5), target + timedelta(days=5))
    todays_games = [game for game in games if game.get("officialDate") == target.isoformat()]
    filename = report_filename(target, todays_games)
    destination = Path(filename)
    if destination.exists() and not args.overwrite:
        print(f"Report already exists: {destination}")
        write_github_output("created", "false")
        write_github_output("report", filename)
        return 0

    previous_games = [g for g in games if (g.get("officialDate") or "") < target.isoformat()]
    next_games = [g for g in games if (g.get("officialDate") or "") > target.isoformat()]
    context = {
        "report_date": target.isoformat(),
        "todays_games": [game_summary(g) for g in todays_games],
        "most_recent_game": game_summary(previous_games[-1]) if previous_games else None,
        "next_game": game_summary(next_games[0]) if next_games else None,
    }

    prompt = f"""
Write today's San Francisco Giants scouting report as a complete solo-host audio script
for the BUSTER BALL PODCAST, hosted only by Billy B. Return only clean Markdown.

Report date and official MLB schedule context:
{json.dumps(context, indent=2)}

Use web search to verify every current fact. Prefer MLB, Baseball Savant, FanGraphs,
Baseball-Reference, and official team reporting. Do not invent statistics, injuries,
probable pitchers, roster moves, or lineups. Label uncertainty clearly.

Requirements:
- Maximum 6,600 characters total.
- Conversational advanced-sabermetrics podcast voice, written to be spoken aloud.
- For a game day: recap the latest Giants action briefly, set the matchup, probable
  pitchers, bullpen condition, key hitters, injuries/roster changes, attack plans,
  leverage and times-through-order strategy, three tactical keys, and a score prediction.
- For an off-day: recap the most recent game/series and scout the next opponent.
- Use the most relevant available metrics: wRC+, wOBA/xwOBA, ISO, WAR, FIP/xFIP,
  K-BB%, Whiff%, chase rate, Hard-Hit%, Barrel%, WPA, leverage, and run value.
  Do not force a metric when reliable current data is unavailable.
- Include a concise Scout's Notebook and Road to 100 update when mathematically relevant.
- Use Markdown headings, but do not include a bibliography or raw URLs.
- End with: “That’s today’s Buster Ball Podcast scouting report. I’m Billy B.”
""".strip()

    client = OpenAI()
    response = client.responses.create(
        model=args.model,
        tools=[{"type": "web_search"}],
        input=prompt,
    )
    report = clean_markdown(response.output_text)
    if len(report) > MAX_REPORT_CHARACTERS:
        report = shorten_report(client, args.model, report)
    report = enforce_limit(report)
    if not report.startswith("#"):
        report = f"# BUSTER BALL PODCAST — Giants Daily Scouting Report\n\n{report}"
        report = enforce_limit(report)

    destination.write_text(report, encoding="utf-8")
    print(f"Created {destination} ({len(report)} characters)")
    write_github_output("created", "true")
    write_github_output("report", filename)
    return 0


if __name__ == "__main__":
    sys.exit(main())
