"""
Lab (Jack Labadia) — Mountain Project weekly tick tracker.

fetch(since, until, state) -> list[Accomplishment]

Detects:
  - Weekly climbing summary: tick count, grades range, areas visited
  - Clean sends: each Onsight, Flash, or Redpoint with grade and style
  - New all-time hardest clean send (vs state)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from accomplishment import Accomplishment
from sources import mountainproject as mp

logger = logging.getLogger(__name__)

_TZ = ZoneInfo("America/Los_Angeles")
_FRIEND = "lab"
_SOURCE = "mountainproject"
_USER_PATH = "201311703/jack-labadia"


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------

def _weekly_summary(ticks: list[dict], since: datetime) -> list[Accomplishment]:
    if not ticks:
        return []

    grades = [t["Rating"] for t in ticks if t.get("Rating")]
    areas = sorted({mp.area_from_location(t.get("Location", "")) for t in ticks if t.get("Location")})
    clean = [t for t in ticks if mp.is_clean_send(t)]

    # Grade range by Rating Code
    coded = [t for t in ticks if t["rating_code_int"] > 0]
    if coded:
        hardest = max(coded, key=lambda t: t["rating_code_int"])
        easiest = min(coded, key=lambda t: t["rating_code_int"])
        if hardest["Route"] == easiest["Route"]:
            grade_range = hardest["Rating"]
        else:
            grade_range = f"{easiest['Rating']} – {hardest['Rating']}"
    else:
        grade_range = "various"

    area_str = ", ".join(areas[:3]) + (" +more" if len(areas) > 3 else "")
    summary = (
        f"{len(ticks)} tick{'s' if len(ticks) != 1 else ''} "
        f"({len(clean)} clean send{'s' if len(clean) != 1 else ''}), "
        f"{grade_range} @ {area_str}"
    )
    return [Accomplishment(
        friend=_FRIEND,
        source=_SOURCE,
        type="weekly_climbing_summary",
        timestamp=since,
        summary=summary,
        metrics={
            "tick_count": len(ticks),
            "clean_send_count": len(clean),
            "grade_range": grade_range,
            "areas": areas,
        },
    )]


def _clean_sends(ticks: list[dict]) -> list[Accomplishment]:
    results = []
    for t in ticks:
        if not mp.is_clean_send(t):
            continue
        style = t.get("Lead Style", "")
        route = t["Route"]
        grade = t.get("Rating", "?")
        area = mp.area_from_location(t.get("Location", ""))
        notes = t.get("Notes", "").strip()
        ts = datetime.combine(t["date_obj"], datetime.min.time()).replace(tzinfo=_TZ)

        summary = f"{style}: {route} ({grade}) @ {area}"
        results.append(Accomplishment(
            friend=_FRIEND,
            source=_SOURCE,
            type="clean_send",
            timestamp=ts,
            summary=summary,
            metrics={
                "route": route,
                "grade": grade,
                "rating_code": t["rating_code_int"],
                "style": style,
                "route_type": t.get("Route Type", ""),
                "pitches": t.get("Pitches", ""),
                "area": area,
                "notes": notes[:200] if notes else "",
                "url": t.get("URL", ""),
            },
        ))
    return results


def _new_hardest_grade(ticks: list[dict], state: dict) -> list[Accomplishment]:
    clean = [t for t in ticks if mp.is_clean_send(t) and t["rating_code_int"] > 0]
    if not clean:
        return []

    prev_code: int = state.get("hardest_clean_send_code", 0)
    prev_grade: str = state.get("hardest_clean_send_grade", "")

    week_best = max(clean, key=lambda t: t["rating_code_int"])
    new_code = week_best["rating_code_int"]
    new_grade = week_best["Rating"]

    # Always update state
    if new_code > prev_code:
        state["hardest_clean_send_code"] = new_code
        state["hardest_clean_send_grade"] = new_grade

    if prev_code == 0:
        logger.info("lab: seeding hardest clean send = %s (%d)", new_grade, new_code)
        return []

    if new_code <= prev_code:
        return []

    style = week_best.get("Lead Style", "send")
    ts = datetime.combine(week_best["date_obj"], datetime.min.time()).replace(tzinfo=_TZ)
    return [Accomplishment(
        friend=_FRIEND,
        source=_SOURCE,
        type="new_hardest_grade",
        timestamp=ts,
        summary=f"New hardest clean send: {week_best['Route']} {new_grade} ({style}) — was {prev_grade}",
        metrics={
            "route": week_best["Route"],
            "new_grade": new_grade,
            "new_rating_code": new_code,
            "previous_grade": prev_grade,
            "previous_rating_code": prev_code,
            "style": style,
            "url": week_best.get("URL", ""),
        },
    )]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def fetch(since: datetime, until: datetime, state: dict) -> list[Accomplishment]:
    all_ticks = mp.get_ticks(_USER_PATH)
    week_ticks = mp.ticks_in_window(all_ticks, since.date(), until.date())

    results: list[Accomplishment] = []
    results.extend(_weekly_summary(week_ticks, since))
    results.extend(_clean_sends(week_ticks))
    results.extend(_new_hardest_grade(week_ticks, state))

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
    print(f"Fetching Lab's ticks {since.date()} → {until.date()}\n")
    state: dict = {}
    results = fetch(since, until, state)
    if not results:
        print("No ticks found.")
    else:
        for r in results:
            print(f"  [{r.type}]\n  {r.summary}\n")
    print(f"state after: {state}")
