import os
import time
import logging
import tempfile
from pathlib import Path

import requests
from dotenv import dotenv_values, set_key

logger = logging.getLogger(__name__)

_ENV_PATH = Path(__file__).parent.parent / ".env"

OAUTH_URL = "https://www.strava.com/oauth/token"
API_BASE = "https://www.strava.com/api/v3"

_ACTIVITY_KEYS = (
    "id", "name", "sport_type", "start_date_local",
    "distance", "total_elevation_gain", "moving_time",
)


def _env(key: str) -> str:
    val = dotenv_values(_ENV_PATH).get(key) or os.environ.get(key, "")
    if not val:
        raise RuntimeError(f"Missing required env var: {key}")
    return val


def _write_refresh_token(new_token: str) -> None:
    """Atomically update STRAVA_REFRESH_TOKEN in .env."""
    tmp = _ENV_PATH.with_suffix(".env.tmp")
    lines = _ENV_PATH.read_text().splitlines(keepends=True)
    updated = []
    found = False
    for line in lines:
        if line.startswith("STRAVA_REFRESH_TOKEN="):
            updated.append(f"STRAVA_REFRESH_TOKEN={new_token}\n")
            found = True
        else:
            updated.append(line)
    if not found:
        updated.append(f"STRAVA_REFRESH_TOKEN={new_token}\n")
    tmp.write_text("".join(updated))
    tmp.rename(_ENV_PATH)


def get_access_token(
    client_id_key: str = "STRAVA_CLIENT_ID",
    client_secret_key: str = "STRAVA_CLIENT_SECRET",
    refresh_token_key: str = "STRAVA_REFRESH_TOKEN",
) -> str:
    resp = requests.post(OAUTH_URL, data={
        "client_id": _env(client_id_key),
        "client_secret": _env(client_secret_key),
        "refresh_token": _env(refresh_token_key),
        "grant_type": "refresh_token",
    }, timeout=15)

    if resp.status_code != 200:
        raise RuntimeError(f"Strava token refresh failed {resp.status_code}: {resp.text}")

    data = resp.json()
    new_refresh = data.get("refresh_token")
    if new_refresh and new_refresh != _env(refresh_token_key):
        _write_refresh_token(new_refresh)
        logger.debug("Strava refresh token rotated")

    return data["access_token"]


def _slim(activity: dict) -> dict:
    a = {k: activity.get(k) for k in _ACTIVITY_KEYS}
    a["distance_mi"] = round((a["distance"] or 0) / 1609.344, 2)
    a["elevation_ft"] = round((a["total_elevation_gain"] or 0) * 3.28084, 1)
    a["moving_time_hr"] = round((a["moving_time"] or 0) / 3600, 2)
    return a


def get_activities(
    after: int,
    before: int,
    client_id_key: str = "STRAVA_CLIENT_ID",
    client_secret_key: str = "STRAVA_CLIENT_SECRET",
    refresh_token_key: str = "STRAVA_REFRESH_TOKEN",
) -> list[dict]:
    token = get_access_token(client_id_key, client_secret_key, refresh_token_key)
    activities: list[dict] = []
    page = 1

    while True:
        resp = _fetch_page(token, after, before, page,
                           client_id_key, client_secret_key, refresh_token_key)
        batch = resp.json()
        if not batch:
            break
        activities.extend(_slim(a) for a in batch)
        if len(batch) < 200:
            break
        page += 1

    return activities


def _fetch_page(
    token: str,
    after: int,
    before: int,
    page: int,
    client_id_key: str,
    client_secret_key: str,
    refresh_token_key: str,
    _retried: bool = False,
) -> requests.Response:
    resp = requests.get(
        f"{API_BASE}/athlete/activities",
        headers={"Authorization": f"Bearer {token}"},
        params={"after": after, "before": before, "per_page": 200, "page": page},
        timeout=30,
    )

    if resp.status_code == 401 and not _retried:
        logger.warning("Strava 401 — refreshing token and retrying")
        token = get_access_token(client_id_key, client_secret_key, refresh_token_key)
        return _fetch_page(token, after, before, page,
                           client_id_key, client_secret_key, refresh_token_key,
                           _retried=True)

    if resp.status_code == 429:
        limit = resp.headers.get("X-RateLimit-Limit", "?")
        usage = resp.headers.get("X-RateLimit-Usage", "?")
        reset = resp.headers.get("X-ReadRateLimit-Reset", "?")
        raise RuntimeError(
            f"Strava rate limit hit (limit={limit}, usage={usage}, reset={reset})"
        )

    if not resp.ok:
        raise RuntimeError(f"Strava API error {resp.status_code}: {resp.text}")

    return resp
