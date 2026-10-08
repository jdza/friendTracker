"""
Jake — chess.com weekly tracker.

fetch(since, until, state) -> list[Accomplishment]

Detects:
  - New all-time-high rating (rapid / blitz / bullet)
  - Weekly record summary per time control
  - Hot streak: longest consecutive-win run in the week (≥3)
  - Great week: ≥55% win rate across all time controls (min 5 games)
  - Upset win: beat an opponent rated 100+ higher
  - High accuracy: game where Jake's accuracy ≥ 90
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

_TRACKED = ("rapid", "blitz", "bullet")
_GREAT_WEEK_THRESHOLD = 0.55
_GREAT_WEEK_MIN_GAMES = 5
_STREAK_MIN = 3
_UPSET_GAP = 100
_HIGH_ACCURACY = 90.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _game_ts(game: dict) -> datetime:
    return datetime.fromtimestamp(game["end_time"], tz=_TZ)


def _tracked_games(games: list[dict]) -> list[dict]:
    return [g for g in games if g.get("time_class") in _TRACKED]


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------

def _rating_highs(stats: dict, state: dict) -> list[Accomplishment]:
    results = []
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
            ts = datetime.fromtimestamp(best_block.get("date", 0), tz=_TZ)
            summary = (
                f"New all-time {tc} high: {current_best} (was {prev})"
                if prev is not None
                else f"All-time {tc} best: {current_best}"
            )
            results.append(Accomplishment(
                friend=_FRIEND,
                source=_SOURCE,
                type=f"new_{tc}_rating_high",
                timestamp=ts,
                summary=summary,
                metrics={
                    "time_control": tc,
                    "new_best": current_best,
                    "previous_best": prev,
                    "game_url": best_block.get("game"),
                },
            ))

    state["best_ratings"] = new_bests
    return results


def _weekly_record(games: list[dict], since: datetime) -> list[Accomplishment]:
    results = []
    by_tc: dict[str, dict] = defaultdict(lambda: {"wins": 0, "losses": 0, "draws": 0})

    for g in _tracked_games(games):
        outcome = chesscom.parse_result(g, _USERNAME)
        tc = g["time_class"]
        if outcome == "win":
            by_tc[tc]["wins"] += 1
        elif outcome == "loss":
            by_tc[tc]["losses"] += 1
        elif outcome == "draw":
            by_tc[tc]["draws"] += 1

    for tc, c in by_tc.items():
        total = c["wins"] + c["losses"] + c["draws"]
        if total == 0:
            continue
        win_pct = round(100 * c["wins"] / total)
        results.append(Accomplishment(
            friend=_FRIEND,
            source=_SOURCE,
            type=f"weekly_{tc}_record",
            timestamp=since,
            summary=f"{c['wins']}W / {c['losses']}L / {c['draws']}D in {tc} ({win_pct}% win rate)",
            metrics={
                "time_control": tc,
                "wins": c["wins"],
                "losses": c["losses"],
                "draws": c["draws"],
                "total_games": total,
                "win_pct": win_pct,
            },
        ))
    return results


def _hot_streak(games: list[dict], since: datetime) -> list[Accomplishment]:
    """Longest consecutive-win run across all time controls in the week."""
    ordered = sorted(_tracked_games(games), key=lambda g: g["end_time"])

    best_streak: list[dict] = []
    current: list[dict] = []

    for g in ordered:
        if chesscom.parse_result(g, _USERNAME) == "win":
            current.append(g)
            if len(current) > len(best_streak):
                best_streak = list(current)
        else:
            current = []

    if len(best_streak) < _STREAK_MIN:
        return []

    streak_end = _game_ts(best_streak[-1])
    tcs = sorted({g["time_class"] for g in best_streak})
    return [Accomplishment(
        friend=_FRIEND,
        source=_SOURCE,
        type="hot_streak",
        timestamp=streak_end,
        summary=f"{len(best_streak)}-game win streak ({', '.join(tcs)})",
        metrics={
            "streak_length": len(best_streak),
            "time_controls": tcs,
            "game_urls": [g["url"] for g in best_streak],
        },
    )]


def _great_week(games: list[dict], since: datetime) -> list[Accomplishment]:
    """Overall win rate ≥55% across all time controls, min 5 games."""
    tracked = _tracked_games(games)
    wins = sum(1 for g in tracked if chesscom.parse_result(g, _USERNAME) == "win")
    losses = sum(1 for g in tracked if chesscom.parse_result(g, _USERNAME) == "loss")
    draws = sum(1 for g in tracked if chesscom.parse_result(g, _USERNAME) == "draw")
    total = wins + losses + draws

    if total < _GREAT_WEEK_MIN_GAMES:
        return []
    win_rate = wins / total
    if win_rate < _GREAT_WEEK_THRESHOLD:
        return []

    win_pct = round(100 * win_rate)
    return [Accomplishment(
        friend=_FRIEND,
        source=_SOURCE,
        type="great_week",
        timestamp=since,
        summary=f"Great week: {wins}W / {losses}L / {draws}D across all controls ({win_pct}%)",
        metrics={
            "wins": wins,
            "losses": losses,
            "draws": draws,
            "total_games": total,
            "win_pct": win_pct,
        },
    )]


def _upset_wins(games: list[dict]) -> list[Accomplishment]:
    """Beat an opponent rated 100+ higher."""
    results = []
    for g in _tracked_games(games):
        if chesscom.parse_result(g, _USERNAME) != "win":
            continue
        my_rating = chesscom.player_rating_in_game(g, _USERNAME)
        opp = chesscom.opponent_info(g, _USERNAME)
        if not opp or my_rating is None:
            continue
        opp_rating = opp.get("rating")
        if opp_rating is None:
            continue
        gap = opp_rating - my_rating
        if gap < _UPSET_GAP:
            continue
        opening = chesscom.opening_name(g)
        results.append(Accomplishment(
            friend=_FRIEND,
            source=_SOURCE,
            type="upset_win",
            timestamp=_game_ts(g),
            summary=(
                f"Upset: beat {opp['username']} ({opp_rating}) "
                f"as {my_rating} (+{gap}) playing {opening}"
            ),
            metrics={
                "jake_rating": my_rating,
                "opponent": opp["username"],
                "opponent_rating": opp_rating,
                "rating_gap": gap,
                "time_control": g["time_class"],
                "opening": opening,
                "game_url": g["url"],
            },
        ))
    return results


def _high_accuracy_games(games: list[dict]) -> list[Accomplishment]:
    """Games where Jake's accuracy ≥ 90."""
    results = []
    for g in _tracked_games(games):
        acc = chesscom.player_accuracy(g, _USERNAME)
        if acc is None or acc < _HIGH_ACCURACY:
            continue
        opp = chesscom.opponent_info(g, _USERNAME)
        opp_name = opp["username"] if opp else "opponent"
        outcome = chesscom.parse_result(g, _USERNAME) or "unknown"
        opening = chesscom.opening_name(g)
        results.append(Accomplishment(
            friend=_FRIEND,
            source=_SOURCE,
            type="high_accuracy_game",
            timestamp=_game_ts(g),
            summary=f"{acc:.1f}% accuracy ({outcome}) vs {opp_name} — {opening}",
            metrics={
                "accuracy": acc,
                "outcome": outcome,
                "opponent": opp_name,
                "time_control": g["time_class"],
                "opening": opening,
                "game_url": g["url"],
            },
        ))
    return sorted(results, key=lambda r: r.metrics["accuracy"], reverse=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def fetch(since: datetime, until: datetime, state: dict) -> list[Accomplishment]:
    after = int(since.timestamp())
    before = int(until.timestamp())

    stats = chesscom.get_stats(_USERNAME)
    games = chesscom.get_games_for_week(_USERNAME, after, before)

    results: list[Accomplishment] = []
    results.extend(_rating_highs(stats, state))
    results.extend(_weekly_record(games, since))
    results.extend(_hot_streak(games, since))
    results.extend(_great_week(games, since))
    results.extend(_upset_wins(games))
    results.extend(_high_accuracy_games(games))

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
    print(f"Fetching Jake's chess.com activity {since.date()} → {until.date()}\n")
    state: dict = {}
    results = fetch(since, until, state)
    if not results:
        print("No accomplishments found.")
    else:
        for r in results:
            print(f"  [{r.type}]\n  {r.summary}\n")
    print(f"state after: {json.dumps(state)}")
