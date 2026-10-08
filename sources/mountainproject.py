import csv
import io
import logging
from datetime import date
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_UA = "friendtracker/1.0 (github.com/jdza/friendTracker)"

# Lead styles that count as a clean send
CLEAN_STYLES = {"Onsight", "Flash", "Redpoint"}


def get_ticks(user_path: str) -> list[dict]:
    """
    Fetch and parse a user's full tick CSV.

    user_path: the path segment, e.g. "201311703/jack-labadia"
    Returns rows as dicts with keys matching the CSV columns, plus
    a parsed 'date_obj' (date) and 'rating_code_int' (int, 0 if absent).
    """
    url = f"https://www.mountainproject.com/user/{user_path}/tick-export"
    resp = requests.get(url, headers={"User-Agent": _UA}, timeout=45)
    if not resp.ok:
        raise RuntimeError(f"Mountain Project fetch failed {resp.status_code}: {url}")
    if "text/csv" not in resp.headers.get("content-type", ""):
        raise RuntimeError(f"Mountain Project returned non-CSV content for {url}")

    reader = csv.DictReader(io.StringIO(resp.text))
    rows = []
    for row in reader:
        try:
            row["date_obj"] = date.fromisoformat(row["Date"])
        except ValueError:
            row["date_obj"] = None
        try:
            row["rating_code_int"] = int(row.get("Rating Code") or 0)
        except ValueError:
            row["rating_code_int"] = 0
        rows.append(row)
    return rows


def ticks_in_window(ticks: list[dict], since: date, until: date) -> list[dict]:
    return [t for t in ticks if t["date_obj"] and since <= t["date_obj"] <= until]


def is_clean_send(tick: dict) -> bool:
    return tick.get("Lead Style", "") in CLEAN_STYLES


def area_from_location(location: str) -> str:
    """Return the most specific area segment from a '>' delimited location string."""
    parts = [p.strip() for p in location.split(">") if p.strip()]
    return parts[-1] if parts else location
