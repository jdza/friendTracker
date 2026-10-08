"""
Spotify artist stats.

Play counts and monthly listeners aren't in Spotify's public Web API, so we
scrape them the same way open.spotify.com gets them: load the artist and
album pages in headless Chromium and capture the web player's own
`pathfinder` GraphQL responses (queryArtistOverview, getAlbum). Letting the
real page run means we never have to reimplement Spotify's anonymous-token
handshake, which changes often. No credentials needed.

Not available logged out: genres (stripped from the Web API for new apps),
"Discovered On" playlists (returned as errors), and song credits.

Requires: pip install playwright && python -m playwright install chromium
"""

import logging
from datetime import date, datetime
from typing import Any, Optional

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
    if not d:
        return None
    if d.get("isoString"):
        return datetime.fromisoformat(d["isoString"].replace("Z", "+00:00")).date()
    if d.get("year"):
        return date(d["year"], 1, 1)
    return None


def _largest_image(sources: list[dict]) -> Optional[str]:
    if not sources:
        return None
    return max(sources, key=lambda s: (s.get("width") or 0))["url"]


def _uri_id(uri: str) -> str:
    return uri.rsplit(":", 1)[-1]


def _find(obj: Any, key: str) -> Any:
    """First value for key anywhere in a nested dict/list (for loosely-shaped fields)."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        children = obj.values()
    elif isinstance(obj, list):
        children = obj
    else:
        return None
    for child in children:
        found = _find(child, key)
        if found is not None:
            return found
    return None


# ---------------------------------------------------------------------------
# Artist-page parsers
# ---------------------------------------------------------------------------

def _release_refs(discography: dict) -> list[dict]:
    """Every release (album / single / EP / compilation) listed on the artist page."""
    refs: dict[str, dict] = {}
    for group in ("albums", "singles", "compilations"):
        for item in (discography.get(group) or {}).get("items", []):
            for rel in item.get("releases", {}).get("items", []):
                refs[rel["uri"]] = {
                    "id": rel["id"],
                    "name": rel["name"],
                    "type": rel.get("type", group.rstrip("s").upper()),
                    "release_date": _parse_date(rel.get("date")),
                }
    return list(refs.values())


def _playlists(items: list[dict]) -> list[dict]:
    out = []
    for it in items:
        d = it.get("data", {})
        if d.get("__typename") != "Playlist":
            continue
        out.append({
            "id": _uri_id(d["uri"]),
            "name": d.get("name", ""),
            "owner": (d.get("ownerV2") or {}).get("data", {}).get("name", ""),
            "url": f"{_WEB}/playlist/{_uri_id(d['uri'])}",
        })
    return out


def _appears_on(items: list[dict]) -> list[dict]:
    out = []
    for it in items:
        for rel in it.get("releases", {}).get("items", []):
            out.append({
                "id": rel["id"],
                "name": rel.get("name", ""),
                "type": rel.get("type", ""),
                "artists": [a["profile"]["name"] for a in rel.get("artists", {}).get("items", [])],
                "release_date": _parse_date(rel.get("date")),
                "url": f"{_WEB}/album/{rel['id']}",
            })
    return out


def _concerts(items: list[dict]) -> list[dict]:
    out = []
    for it in items:
        d = it.get("data", {})
        if not d.get("uri"):
            continue
        loc = d.get("location") or {}
        out.append({
            "id": _uri_id(d["uri"]),
            "title": d.get("title", ""),
            "venue": loc.get("name", ""),
            "city": loc.get("city", ""),
            "start": d.get("startDateIsoString", ""),
            "festival": bool(d.get("festival")),
            "url": f"{_WEB}/concert/{_uri_id(d['uri'])}",
        })
    return out


def _pre_release(pre: Optional[dict]) -> Optional[dict]:
    """An announced-but-unreleased (countdown) release, if Spotify shows one."""
    if not pre:
        return None
    uri = _find(pre, "uri") or ""
    when = _find(pre, "isoString") or _find(pre, "releaseDate")
    if isinstance(when, dict):
        parsed = _parse_date(when)
        when = parsed.isoformat() if parsed else None
    return {
        "id": _uri_id(uri) if uri else None,
        "name": _find(pre, "name") or "",
        "release_date": when if isinstance(when, str) else None,
    }


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------

def get_artist_snapshot(artist_id: str) -> dict:
    """
    Scrape a point-in-time snapshot of an artist's Spotify presence.

    Returns:
      {
        "artist_id", "name", "monthly_listeners", "followers", "world_rank",
        "verified", "biography", "image_url", "external_links": [{"name", "url"}],
        "top_cities":       [{"city", "region", "country", "listeners"}],
        "releases":         [{"id", "name", "type", "release_date", "label", "track_count", "url"}],
        "tracks":           [{"id", "name", "playcount", "duration_ms", "explicit",
                              "release", "release_date", "artists", "url"}],
        "featured_on":      [{"id", "name", "owner", "url"}],   # Spotify playlists featuring them
        "artist_playlists": [{"id", "name", "owner", "url"}],   # playlists on their profile
        "appears_on":       [{"id", "name", "type", "artists", "release_date", "url"}],
        "concerts":         [{"id", "title", "venue", "city", "start", "festival", "url"}],
        "related_artists":  [str],                               # "Fans also like"
        "pre_release":      {"id", "name", "release_date"} | None,
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
                    "label": album.get("label", ""),
                    "track_count": len(items),
                    "url": f"{_WEB}/album/{ref['id']}",
                })
                for it in items:
                    t = it["track"]
                    tid = _uri_id(t["uri"])
                    row = {
                        "id": tid,
                        "name": t["name"],
                        "playcount": int(t.get("playcount") or 0),
                        "duration_ms": (t.get("duration") or {}).get("totalMilliseconds", 0),
                        "explicit": (t.get("contentRating") or {}).get("label") == "EXPLICIT",
                        "release": album["name"],
                        "release_date": rel_date,
                        "artists": [a["profile"]["name"] for a in t["artists"]["items"]],
                        "url": f"{_WEB}/track/{tid}",
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

    stats = artist.get("stats") or {}
    profile = artist.get("profile") or {}
    related = artist.get("relatedContent") or {}
    goods = artist.get("goods") or {}
    verification = (artist.get("onPlatformReputationTrait") or {}).get("verification") or {}

    return {
        "artist_id": artist_id,
        "name": profile["name"],
        "monthly_listeners": stats.get("monthlyListeners") or 0,
        "followers": stats.get("followers") or 0,
        "world_rank": stats.get("worldRank") or None,
        "verified": bool(verification.get("isVerified")),
        "biography": (profile.get("biography") or {}).get("text", ""),
        "image_url": _largest_image(
            ((artist.get("visuals") or {}).get("avatarImage") or {}).get("sources", [])),
        "external_links": [{"name": l["name"], "url": l["url"]}
                           for l in (profile.get("externalLinks") or {}).get("items", [])],
        "top_cities": [
            {
                "city": c["city"],
                "region": c.get("region", ""),
                "country": c.get("country", ""),
                "listeners": c.get("numberOfListeners", 0),
            }
            for c in (stats.get("topCities") or {}).get("items", [])
        ],
        "releases": sorted(releases, key=lambda r: r["release_date"] or date.min, reverse=True),
        "tracks": sorted(tracks.values(), key=lambda t: t["playcount"], reverse=True),
        "featured_on": _playlists((related.get("featuringV2") or {}).get("items", [])),
        "artist_playlists": _playlists((profile.get("playlistsV2") or {}).get("items", [])),
        "appears_on": _appears_on((related.get("appearsOn") or {}).get("items", [])),
        "concerts": _concerts((goods.get("concerts") or {}).get("items", [])),
        "related_artists": [a["profile"]["name"]
                            for a in (related.get("relatedArtists") or {}).get("items", [])],
        "pre_release": _pre_release(artist.get("preRelease")),
    }
