"""
Steg (Jack Stegelman) — Strava weekly activity tracker.

fetch(since, until, state) -> list[Accomplishment]

Returns:
  - Weekly summary per sport type (miles, elevation, time, count)
  - Longest single activity of the week
  - New all-time longest single activity (vs state, per sport)
  - New all-time best week by mileage (vs state, per sport)
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta
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


def _activity_ts(a: dict) -> datetime:
    return datetime.fromisoformat(a["start_date_local"].replace("Z", "")).replace(tzinfo=_TZ)


def _weekly_summaries(activities: list[dict], since: datetime) -> list[Accomplishment]:
    by_sport: dict[str, list[dict]] = defaultdict(list)
    for a in activities:
        by_sport[a["sport_type"]].append(a)

    results = []
    for sport, acts in by_sport.items():
        total_mi = round(sum(a["distance_mi"] for a in acts), 2)
        total_elev = round(sum(a["elevation_ft"] for a in acts), 1)
        total_hr = round(sum(a["moving_time_hr"] for a in acts), 2)
        count = len(acts)
        results.append(Accomplishment(
            friend=_FRIEND,
            source=_SOURCE,
            type=f"weekly_{sport.lower()}_summary",
            timestamp=since,
            summary=(
                f"{count} {sport.lower()} activit{'y' if count == 1 else 'ies'}: "
                f"{total_mi} mi, {total_elev} ft gain, {total_hr} hr"
            ),
            metrics={
                "sport_type": sport,
                "total_miles": total_mi,
                "total_elevation_ft": total_elev,
                "total_moving_time_hr": total_hr,
                "activity_count": count,
            },
        ))
    return results


def _longest_this_week(activities: list[dict]) -> list[Accomplishment]:
    longest = max(activities, key=lambda a: a["distance_mi"])
    if longest["distance_mi"] <= 0:
        return []
    ts = _activity_ts(longest)
    return [Accomplishment(
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
    )]


def _new_records(activities: list[dict], since: datetime, state: dict) -> list[Accomplishment]:
    best_single: dict[str, float] = state.setdefault("best_single_mi", {})
    best_weekly: dict[str, float] = state.setdefault("best_weekly_mi", {})
    results = []

    # Best single activity per sport this week
    week_best_single: dict[str, dict] = {}
    for a in activities:
        sport = a["sport_type"]
        if sport not in week_best_single or a["distance_mi"] > week_best_single[sport]["distance_mi"]:
            week_best_single[sport] = a

    for sport, a in week_best_single.items():
        mi = a["distance_mi"]
        prev = best_single.get(sport, 0)
        if mi > prev:
            best_single[sport] = mi
            if prev > 0:
                ts = _activity_ts(a)
                results.append(Accomplishment(
                    friend=_FRIEND,
                    source=_SOURCE,
                    type="new_longest_activity_ever",
                    timestamp=ts,
                    summary=(
                        f"New longest {sport.lower()} ever: {mi} mi — \"{a['name']}\" "
                        f"(was {prev} mi)"
                    ),
                    metrics={
                        "sport_type": sport,
                        "name": a["name"],
                        "distance_mi": mi,
                        "previous_best_mi": prev,
                        "strava_id": a["id"],
                    },
                ))

    # Weekly total per sport
    by_sport: dict[str, float] = defaultdict(float)
    for a in activities:
        by_sport[a["sport_type"]] += a["distance_mi"]

    for sport, total_mi in by_sport.items():
        total_mi = round(total_mi, 2)
        prev = best_weekly.get(sport, 0)
        if total_mi > prev:
            best_weekly[sport] = total_mi
            if prev > 0:
                results.append(Accomplishment(
                    friend=_FRIEND,
                    source=_SOURCE,
                    type="new_best_week_ever",
                    timestamp=since,
                    summary=(
                        f"New best {sport.lower()} week ever: {total_mi} mi "
                        f"(was {prev} mi)"
                    ),
                    metrics={
                        "sport_type": sport,
                        "total_miles": total_mi,
                        "previous_best_mi": prev,
                    },
                ))

    return results


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
    results.extend(_weekly_summaries(activities, since))
    results.extend(_longest_this_week(activities))
    results.extend(_new_records(activities, since, state))
    return results


def _last_week() -> tuple[datetime, datetime]:
    today = datetime.now(_TZ).date()
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
