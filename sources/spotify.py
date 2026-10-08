"""
Spotify artist stats.

Play counts and monthly listeners aren't in Spotify's public Web API, so we
scrape them the same way open.spotify.com gets them: load the artist and
album pages in headless Chromium and capture the web player's own
`pathfinder` GraphQL responses (queryArtistOverview, getAlbum). Letting the
real page run means we never have to reimplement Spotify's anonymous-token
handshake, which changes often.

Genres only exist in the official Web API. If SPOTIFY_CLIENT_ID and
SPOTIFY_CLIENT_SECRET are set, get_artist_genres() uses the client-credentials
flow to read them; otherwise it returns None and the rest still works.
Note: development-mode apps created after Feb 2026 don't receive the genres
field at all, so in practice this needs an app with extended quota access.

Requires: pip install playwright && python -m playwright install chromium
"""

import base64
import logging
import os
from datetime import date, datetime
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_WEB = "https://open.spotify.com"
_PAGE_TIMEOUT_MS = 45_000

# Spotify reports play counts under 1,000 as 0 (the web player hides them).
PLAYCOUNT_FLOOR = 1000


def _is_op(response, operation: str) -> bool:
    return "pathfinder" in response.url and operation in (response.request.post_data or "")


def _capture(page, url: str, operation: str) -> dict:
    """Navigate to url and return the JSON body of the named pathfinder operation."""
    with page.expect_response(lambda r: _is_op(r, operation), timeout=_PAGE_TIMEOUT_MS) as info:
        page.goto(url, wait_until="domcontentloaded")
    resp = info.value
    if not resp.ok:
        raise RuntimeError(f"Spotify {operation} failed {resp.status}: {url}")
    body = resp.json()
    if body.get("errors"):
        raise RuntimeError(f"Spotify {operation} returned errors: {body['errors']}")
    return body["data"]


def _parse_date(d: Optional[dict]) -> Optional[date]:
    if not d or not d.get("isoString"):
        return None
    return datetime.fromisoformat(d["isoString"].replace("Z", "+00:00")).date()


def _release_refs(discography: dict) -> list[dict]:
    """Every release (album / single / EP / compilation) listed on the artist page."""
    refs: dict[str, dict] = {}
    for group in ("albums", "singles", "compilations"):
        for item in discography.get(group, {}).get("items", []):
            for rel in item.get("releases", {}).get("items", []):
                refs[rel["uri"]] = {
                    "uri": rel["uri"],
                    "id": rel["id"],
                    "name": rel["name"],
                    "type": rel.get("type", group.rstrip("s").upper()),
                    "release_date": _parse_date(rel.get("date")),
                }
    return list(refs.values())


def get_artist_snapshot(artist_id: str) -> dict:
    """
    Scrape a point-in-time snapshot of an artist's Spotify stats.

    Returns:
      {
        "artist_id", "name", "monthly_listeners", "followers",
        "top_cities": [{"city", "region", "country", "listeners"}],
        "releases":   [{"id", "name", "type", "release_date" (date|None), "track_count"}],
        "tracks":     [{"id", "name", "playcount", "release", "release_date", "artists"}],
      }

    `tracks` is deduplicated by title: the same recording on a single and an
    EP gets separate track IDs but shares one play count, so we keep one row.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            overview = _capture(page, f"{_WEB}/artist/{artist_id}", "queryArtistOverview")
            artist = overview["artistUnion"]
            if artist.get("__typename") != "Artist":
                raise RuntimeError(f"Spotify artist {artist_id} not found")

            releases = []
            tracks: dict[str, dict] = {}
            for ref in _release_refs(artist["discography"]):
                album = _capture(page, f"{_WEB}/album/{ref['id']}", "getAlbum")["albumUnion"]
                items = album["tracksV2"]["items"]
                rel_date = _parse_date(album.get("date")) or ref["release_date"]
                releases.append({
                    "id": ref["id"],
                    "name": album["name"],
                    "type": album.get("type", ref["type"]),
                    "release_date": rel_date,
                    "track_count": len(items),
                })
                for it in items:
                    t = it["track"]
                    row = {
                        "id": t["uri"].rsplit(":", 1)[-1],
                        "name": t["name"],
                        "playcount": int(t.get("playcount") or 0),
                        "release": album["name"],
                        "release_date": rel_date,
                        "artists": [a["profile"]["name"] for a in t["artists"]["items"]],
                    }
                    key = t["name"].strip().lower()
                    prev = tracks.get(key)
                    # Keep the higher count; on ties keep the earliest release.
                    if (prev is None or row["playcount"] > prev["playcount"]
                            or (row["playcount"] == prev["playcount"] and rel_date and prev["release_date"]
                                and rel_date < prev["release_date"])):
                        tracks[key] = row
        finally:
            browser.close()

    stats = artist.get("stats", {})
    return {
        "artist_id": artist_id,
        "name": artist["profile"]["name"],
        "monthly_listeners": stats.get("monthlyListeners") or 0,
        "followers": stats.get("followers") or 0,
        "top_cities": [
            {
                "city": c["city"],
                "region": c.get("region", ""),
                "country": c.get("country", ""),
                "listeners": c.get("numberOfListeners", 0),
            }
            for c in stats.get("topCities", {}).get("items", [])
        ],
        "releases": sorted(releases, key=lambda r: r["release_date"] or date.min, reverse=True),
        "tracks": sorted(tracks.values(), key=lambda t: t["playcount"], reverse=True),
    }


# ---------------------------------------------------------------------------
# Genres — official Web API (optional credentials)
# ---------------------------------------------------------------------------

def _client_credentials_token() -> Optional[str]:
    client_id = os.getenv("SPOTIFY_CLIENT_ID")
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
    if not client_id or not client_secret:
        return None
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    resp = requests.post(
        "https://accounts.spotify.com/api/token",
        headers={"Authorization": f"Basic {basic}"},
        data={"grant_type": "client_credentials"},
        timeout=15,
    )
    if not resp.ok:
        raise RuntimeError(f"Spotify token request failed {resp.status_code}: {resp.text[:200]}")
    return resp.json()["access_token"]


def get_artist_genres(artist_id: str) -> Optional[list[str]]:
    """
    Spotify's genre tags for an artist, or None if credentials aren't
    configured or the app isn't allowed to see genres.

    Spotify only assigns genres once an artist has enough listening data, so
    an empty list is a normal answer for small artists.
    """
    token = _client_credentials_token()
    if token is None:
        return None
    resp = requests.get(
        f"https://api.spotify.com/v1/artists/{artist_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    if not resp.ok:
        raise RuntimeError(f"Spotify artist lookup failed {resp.status_code}: {resp.text[:200]}")
    body = resp.json()
    if "genres" not in body:
        # Development-mode apps created after Feb 2026 get artist objects
        # with genres stripped entirely (not just empty).
        logger.warning("Spotify omitted genres for %s — app likely lacks access to that field", artist_id)
        return None
    return body["genres"]
