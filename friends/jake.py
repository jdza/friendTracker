"""
Jake — chess.com weekly tracker.

fetch(since, until, state) -> list[Accomplishment]

Returns:
  - New wins per time control (count + win rate for the week)
  - New all-time-high rating for any time control where best.rating improved
    since the last run (tracked via state)
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from accomplishment import Accomplishment
from sources import chesscom

logger = logging.getLogger(__name__)

_TZ = ZoneInfo("America/Los_Angeles")
_FRIEND = "jake"
_SOURCE = "chesscom"
_USERNAME = "jawg28"

# Track these time controls; daily chess is slow-paced and less interesting
_TRACKED = ("rapid", "blitz", "bullet")


def fetch(since: datetime, until: datetime, state: dict) -> list[Accomplishment]:
    after = int(since.timestamp())
    before = int(until.timestamp())

    stats = chesscom.get_stats(_USERNAME)
    games = chesscom.get_games_for_week(_USERNAME, after, before)

    results: list[Accomplishment] = []

    # --- New wins per time control ---
    by_tc: dict[str, dict] = defaultdict(lambda: {"wins": 0, "losses": 0, "draws": 0})
    for g in games:
        tc = g.get("time_class")
        if tc not in _TRACKED:
            continue
        outcome = chesscom.parse_result(g, _USERNAME)
        if outcome == "win":
            by_tc[tc]["wins"] += 1
        elif outcome == "loss":
            by_tc[tc]["losses"] += 1
        elif outcome == "draw":
            by_tc[tc]["draws"] += 1

    for tc, counts in by_tc.items():
        wins = counts["wins"]
        if wins == 0:
            continue
        total = wins + counts["losses"] + counts["draws"]
        win_pct = round(100 * wins / total) if total else 0
        results.append(Accomplishment(
            friend=_FRIEND,
            source=_SOURCE,
            type=f"weekly_{tc}_wins",
            timestamp=since,
            summary=f"{wins}W / {counts['losses']}L / {counts['draws']}D in {tc} ({win_pct}% win rate)",
            metrics={
                "time_control": tc,
                "wins": wins,
                "losses": counts["losses"],
                "draws": counts["draws"],
                "total_games": total,
                "win_pct": win_pct,
            },
        ))

    # --- New all-time-high ratings ---
    prev_bests: dict[str, int] = state.get("best_ratings", {})
    new_bests: dict[str, int] = dict(prev_bests)

    for tc in _TRACKED:
        stats_key = chesscom._STATS_KEY[tc]
        best_block = stats.get(stats_key, {}).get("best", {})
        current_best = best_block.get("rating")
        if current_best is None:
            continue

        prev = prev_bests.get(tc)
        if prev is None or current_best > prev:
            new_bests[tc] = current_best
            if prev is not None:
                # Only emit if we've seen this person before (not first run)
                best_ts = datetime.fromtimestamp(
                    best_block.get("date", after), tz=_TZ
                )
                results.append(Accomplishment(
                    friend=_FRIEND,
                    source=_SOURCE,
                    type=f"new_{tc}_rating_high",
                    timestamp=best_ts,
                    summary=f"New all-time {tc} high: {current_best} (was {prev})",
                    metrics={
                        "time_control": tc,
                        "new_best": current_best,
                        "previous_best": prev,
                        "game_url": best_block.get("game"),
                    },
                ))
            else:
                logger.info("jake: seeding %s best rating = %d", tc, current_best)

    state["best_ratings"] = new_bests

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
    import json
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    since, until = _last_week()
    print(f"Fetching Jake's chess.com activity {since.date()} → {until.date()}")
    state: dict = {}
    results = fetch(since, until, state)
    if not results:
        print("No accomplishments found.")
    else:
        for r in results:
            print(f"  [{r.type}] {r.summary}")
    print(f"  state after: {json.dumps(state)}")
