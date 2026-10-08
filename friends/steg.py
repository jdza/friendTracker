"""
Steg (Jack Stegelman) — Strava weekly activity tracker.

fetch(since, until, state) -> list[Accomplishment]

Returns:
  - One weekly summary per sport type (miles, elevation, time, count)
  - One record for the longest single activity
"""

from __future__ import annotations

import sys
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from accomplishment import Accomplishment
from sources import strava

logger = logging.getLogger(__name__)

_TZ = ZoneInfo("America/Los_Angeles")
_FRIEND = "steg"
_SOURCE = "strava"

_CLIENT_ID_KEY = "STRAVA_CLIENT_ID"
_CLIENT_SECRET_KEY = "STRAVA_CLIENT_SECRET"
_REFRESH_TOKEN_KEY = "STRAVA_REFRESH_TOKEN"


def fetch(since: datetime, until: datetime, state: dict) -> list[Accomplishment]:
    after = int(since.timestamp())
    before = int(until.timestamp())

    activities = strava.get_activities(
        after, before,
        client_id_key=_CLIENT_ID_KEY,
        client_secret_key=_CLIENT_SECRET_KEY,
        refresh_token_key=_REFRESH_TOKEN_KEY,
    )

    if not activities:
        return []

    results: list[Accomplishment] = []

    # Per-sport summaries
    by_sport: dict[str, list[dict]] = defaultdict(list)
    for a in activities:
        by_sport[a["sport_type"]].append(a)

    for sport, acts in by_sport.items():
        total_mi = round(sum(a["distance_mi"] for a in acts), 2)
        total_elev = round(sum(a["elevation_ft"] for a in acts), 1)
        total_hr = round(sum(a["moving_time_hr"] for a in acts), 2)
        count = len(acts)

        summary = (
            f"{count} {sport.lower()} activit{'y' if count == 1 else 'ies'}: "
            f"{total_mi} mi, {total_elev} ft gain, {total_hr} hr"
        )
        results.append(Accomplishment(
            friend=_FRIEND,
            source=_SOURCE,
            type=f"weekly_{sport.lower()}_summary",
            timestamp=since,
            summary=summary,
            metrics={
                "sport_type": sport,
                "total_miles": total_mi,
                "total_elevation_ft": total_elev,
                "total_moving_time_hr": total_hr,
                "activity_count": count,
            },
        ))

    # Longest single activity (by distance)
    longest = max(activities, key=lambda a: a["distance_mi"])
    if longest["distance_mi"] > 0:
        ts = datetime.fromisoformat(longest["start_date_local"].replace("Z", "")).replace(tzinfo=_TZ)
        results.append(Accomplishment(
            friend=_FRIEND,
            source=_SOURCE,
            type="longest_activity",
            timestamp=ts,
            summary=(
                f"Longest {longest['sport_type'].lower()}: {longest['distance_mi']} mi "
                f"({longest['elevation_ft']} ft gain) — \"{longest['name']}\""
            ),
            metrics={
                "sport_type": longest["sport_type"],
                "name": longest["name"],
                "distance_mi": longest["distance_mi"],
                "elevation_ft": longest["elevation_ft"],
                "moving_time_hr": longest["moving_time_hr"],
                "strava_id": longest["id"],
            },
        ))

    return results


def _last_week() -> tuple[datetime, datetime]:
    today = datetime.now(_TZ).date()
    # Most recent completed Sunday
    days_since_monday = today.weekday()
    last_monday = today - timedelta(days=days_since_monday + 7)
    last_sunday = last_monday + timedelta(days=6)
    since = datetime(last_monday.year, last_monday.month, last_monday.day, 0, 0, 0, tzinfo=_TZ)
    until = datetime(last_sunday.year, last_sunday.month, last_sunday.day, 23, 59, 59, tzinfo=_TZ)
    return since, until


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    since, until = _last_week()
    print(f"Fetching Steg's activities {since.date()} → {until.date()}")
    results = fetch(since, until, {})
    if not results:
        print("No activities found.")
    else:
        for r in results:
            print(f"  [{r.type}] {r.summary}")
