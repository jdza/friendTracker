"""
pull_data.py — fetch all friends' accomplishments for the most recent completed week.

Usage:
  python pull_data.py                  # last completed week
  python pull_data.py --week 2026-09-29  # backfill: week starting on this Monday
"""

import argparse
import importlib
import json
import logging
import sys
from datetime import datetime, timedelta, date
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv

from accomplishment import Accomplishment

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_TZ = ZoneInfo("America/Los_Angeles")
_STATE_FILE = Path("state.json")
_DATA_DIR = Path("data")


def week_bounds(monday: date) -> tuple[datetime, datetime]:
    sunday = monday + timedelta(days=6)
    since = datetime(monday.year, monday.month, monday.day, 0, 0, 0, tzinfo=_TZ)
    until = datetime(sunday.year, sunday.month, sunday.day, 23, 59, 59, tzinfo=_TZ)
    return since, until


def last_completed_monday() -> date:
    today = datetime.now(_TZ).date()
    days_since_monday = today.weekday()
    # If today is Monday, the last *completed* week ended yesterday (last Sunday)
    return today - timedelta(days=days_since_monday + 7)


def load_state() -> dict:
    if _STATE_FILE.exists():
        return json.loads(_STATE_FILE.read_text())
    return {}


def save_state(state: dict) -> None:
    _STATE_FILE.write_text(json.dumps(state, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--week",
        metavar="YYYY-MM-DD",
        help="Monday of the week to pull (default: last completed week)",
    )
    args = parser.parse_args()

    if args.week:
        monday = date.fromisoformat(args.week)
        if monday.weekday() != 0:
            sys.exit(f"--week must be a Monday; {args.week} is a {monday.strftime('%A')}")
    else:
        monday = last_completed_monday()

    since, until = week_bounds(monday)
    week_key = monday.isoformat()
    logger.info("Pulling week %s → %s", since.date(), until.date())

    with open("friends.yaml") as f:
        config = yaml.safe_load(f)

    state = load_state()
    all_results: list[dict] = []

    for friend_cfg in config["friends"]:
        name = friend_cfg["name"]
        module_path = friend_cfg["module"]
        if name not in state:
            state[name] = {}
        friend_state = state[name]

        try:
            mod = importlib.import_module(module_path)
            results = mod.fetch(since, until, friend_state)
            logger.info("%s: %d accomplishment(s)", name, len(results))
            all_results.extend(r.to_dict() for r in results)
        except Exception as exc:
            logger.error("%s: fetch failed — %s", name, exc)
            # Recorded so the newsletter can say "no data" instead of "quiet week".
            all_results.append(Accomplishment(
                friend=name,
                source=friend_cfg.get("source", ""),
                type="fetch_failed",
                timestamp=since,
                summary="Couldn't fetch data this week",
                metrics={"error": str(exc)[:300]},
            ).to_dict())

    _DATA_DIR.mkdir(exist_ok=True)
    out_path = _DATA_DIR / f"{week_key}.json"
    out_path.write_text(json.dumps(all_results, indent=2))
    logger.info("Wrote %s", out_path)

    save_state(state)


if __name__ == "__main__":
    main()
