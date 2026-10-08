"""
seed_state.py — one-time historical lookback to populate state.json.

Run this once before the first weekly pull to establish true all-time
baselines. After this, pull_data.py will only fire "new record"
accomplishments when something genuinely new happens.

  python seed_state.py
  python seed_state.py --dry-run   # print what would be written, don't save
"""

import argparse
import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_TZ = ZoneInfo("America/Los_Angeles")
_STATE_FILE = Path("state.json")
_UA = "friendtracker/1.0 (github.com/jdza/friendTracker)"


# ---------------------------------------------------------------------------
# Jake — chess.com all-time best ratings
# ---------------------------------------------------------------------------

def seed_jake(state: dict) -> None:
    logger.info("jake: fetching all-time ratings from chess.com stats API")
    resp = requests.get(
        "https://api.chess.com/pub/player/jawg28/stats",
        headers={"User-Agent": _UA}, timeout=15,
    )
    if not resp.ok:
        raise RuntimeError(f"chess.com stats failed: {resp.status_code}")

    stats = resp.json()
    tc_map = {"rapid": "chess_rapid", "blitz": "chess_blitz", "bullet": "chess_bullet"}
    best_ratings = {}
    for tc, key in tc_map.items():
        best = stats.get(key, {}).get("best", {}).get("rating")
        if best:
            best_ratings[tc] = best
            logger.info("  jake.%s all-time best: %d", tc, best)

    state["jake"] = {"best_ratings": best_ratings}


# ---------------------------------------------------------------------------
# Lab — Mountain Project all-time hardest clean send
# ---------------------------------------------------------------------------

def seed_lab(state: dict) -> None:
    import csv, io
    logger.info("lab: scanning all ticks for hardest clean send")
    resp = requests.get(
        "https://www.mountainproject.com/user/201311703/jack-labadia/tick-export",
        headers={"User-Agent": "Mozilla/5.0 (compatible; friendtracker/1.0)"},
        timeout=45,
    )
    if not resp.ok or "text/csv" not in resp.headers.get("content-type", ""):
        raise RuntimeError(f"Mountain Project tick export failed: {resp.status_code}")

    clean_styles = {"Onsight", "Flash", "Redpoint"}
    best_code = 0
    best_grade = ""
    total = 0

    for row in csv.DictReader(io.StringIO(resp.text)):
        if row.get("Lead Style") not in clean_styles:
            continue
        try:
            code = int(row.get("Rating Code") or 0)
        except ValueError:
            code = 0
        if code > best_code:
            best_code = code
            best_grade = row.get("Rating", "")
        total += 1

    logger.info("  lab: %d clean sends scanned, hardest = %s (%d)", total, best_grade, best_code)
    state["lab"] = {
        "hardest_clean_send_code": best_code,
        "hardest_clean_send_grade": best_grade,
    }


# ---------------------------------------------------------------------------
# Steg — Strava all-time longest ride and best week per sport
# ---------------------------------------------------------------------------

def _week_monday(dt_str: str) -> str:
    dt = datetime.fromisoformat(dt_str.replace("Z", "")).replace(tzinfo=_TZ)
    monday = dt.date() - timedelta(days=dt.date().weekday())
    return monday.isoformat()


def seed_steg(state: dict) -> None:
    from sources import strava
    logger.info("steg: fetching all Strava activities (this may take a moment)")
    all_acts = strava.get_all_activities()
    logger.info("  steg: %d total activities fetched", len(all_acts))

    best_single: dict[str, float] = {}
    by_week_sport: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))

    for a in all_acts:
        sport = a["sport_type"]
        mi = a["distance_mi"]
        if mi <= 0:
            continue
        if mi > best_single.get(sport, 0):
            best_single[sport] = mi
        wk = _week_monday(a["start_date_local"])
        by_week_sport[sport][wk] += mi

    best_weekly: dict[str, float] = {
        sport: round(max(weeks.values()), 2)
        for sport, weeks in by_week_sport.items()
        if weeks
    }

    for sport in sorted(best_single):
        logger.info("  steg.%s  longest: %.2f mi  best week: %.2f mi",
                    sport, best_single[sport], best_weekly.get(sport, 0))

    state["steg"] = {
        "best_single_mi": {k: round(v, 2) for k, v in best_single.items()},
        "best_weekly_mi": best_weekly,
    }


# ---------------------------------------------------------------------------
# Asher — Spotify monthly-listener baseline
# ---------------------------------------------------------------------------

def seed_asher(state: dict) -> None:
    # Spotify has no listening history to scan, so the baseline is today's
    # monthly listeners. Weekly stream snapshots build up from pull_data.py.
    from sources import spotify
    logger.info("asher: scraping current Spotify stats")
    snap = spotify.get_artist_snapshot("0mt9ovmSTm6oRJyZnr3EZS")
    logger.info("  asher: %d monthly listeners, %d followers, %d tracks",
                snap["monthly_listeners"], snap["followers"], len(snap["tracks"]))

    asher = state.get("asher", {})
    asher["best_monthly_listeners"] = max(
        snap["monthly_listeners"], asher.get("best_monthly_listeners", 0))
    state["asher"] = asher


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Print state that would be written without saving")
    args = parser.parse_args()

    existing = json.loads(_STATE_FILE.read_text()) if _STATE_FILE.exists() else {}
    state = dict(existing)

    seed_jake(state)
    seed_lab(state)
    seed_steg(state)
    seed_asher(state)

    if args.dry_run:
        print(json.dumps(state, indent=2))
    else:
        _STATE_FILE.write_text(json.dumps(state, indent=2))
        logger.info("Wrote %s", _STATE_FILE)


if __name__ == "__main__":
    main()
