import logging
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger(__name__)

_BASE = "https://api.chess.com/pub"
_UA = "friendtracker/1.0 (github.com/jdza/friendTracker)"

# time_class values returned by the games API
TIME_CLASSES = ("rapid", "blitz", "bullet", "daily")

# Mapping from games API time_class to stats API key
_STATS_KEY = {
    "rapid": "chess_rapid",
    "blitz": "chess_blitz",
    "bullet": "chess_bullet",
    "daily": "chess_daily",
}


def _get(url: str) -> dict:
    resp = requests.get(url, headers={"User-Agent": _UA}, timeout=15)
    if resp.status_code == 404:
        return {}
    if not resp.ok:
        raise RuntimeError(f"chess.com API error {resp.status_code}: {url}")
    return resp.json()


def get_stats(username: str) -> dict:
    """Return the raw stats blob for a player."""
    return _get(f"{_BASE}/player/{username}/stats")


def get_games_for_week(username: str, after: int, before: int) -> list[dict]:
    """
    Return all rated games for username with end_time in [after, before].
    Fetches the minimum set of monthly archives that can overlap the window.
    """
    archives_data = _get(f"{_BASE}/player/{username}/games/archives")
    archives: list[str] = archives_data.get("archives", [])

    # Determine which year/month archives overlap the window.
    # A monthly archive at YYYY/MM can contain games from the 1st to last day
    # of that month. We conservatively include any archive whose month
    # contains any day in [after, before].
    since_dt = datetime.fromtimestamp(after, tz=timezone.utc)
    until_dt = datetime.fromtimestamp(before, tz=timezone.utc)
    needed: set[str] = set()
    for url in archives:
        parts = url.rstrip("/").split("/")
        year, month = int(parts[-2]), int(parts[-1])
        # First second of the month and first second of the next month
        month_start = datetime(year, month, 1, tzinfo=timezone.utc)
        if month == 12:
            month_end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            month_end = datetime(year, month + 1, 1, tzinfo=timezone.utc)
        if month_start < until_dt and month_end > since_dt:
            needed.add(url)

    games: list[dict] = []
    for url in sorted(needed):
        data = _get(f"{url}")
        for g in data.get("games", []):
            if not g.get("rated"):
                continue
            if after <= g.get("end_time", 0) <= before:
                games.append(g)

    return games


def parse_result(game: dict, username: str) -> Optional[str]:
    """Return 'win', 'loss', 'draw', or None for an unknown result."""
    uname = username.lower()
    for color in ("white", "black"):
        player = game.get(color, {})
        if player.get("username", "").lower() == uname:
            result = player.get("result", "")
            if result == "win":
                return "win"
            if result in ("checkmated", "resigned", "timeout", "abandoned",
                          "threecheck", "bughousepartnerlose"):
                return "loss"
            if result in ("agreed", "repetition", "stalemate", "insufficient",
                          "50move", "timevsinsufficient", "drawaccepted"):
                return "draw"
            return result  # unknown
    return None


def player_rating_in_game(game: dict, username: str) -> Optional[int]:
    uname = username.lower()
    for color in ("white", "black"):
        player = game.get(color, {})
        if player.get("username", "").lower() == uname:
            return player.get("rating")
    return None
