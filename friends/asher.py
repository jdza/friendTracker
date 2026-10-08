"""
Asher (Asher Friedman) — Spotify streaming tracker.

fetch(since, until, state) -> list[Accomplishment]

Spotify only exposes running totals (lifetime plays per track, current
monthly listeners), not per-week history. So each run stores a snapshot in
state keyed by week, and this week's numbers are the difference from the
most recent earlier week's snapshot. Re-running a week overwrites that
week's snapshot instead of corrupting the deltas.

Because the scrape is always "now", backfilling an old week with --week
reports current totals, not what they were back then.

Detects:
  - Weekly streams summary: plays gained, monthly listeners, followers, top track
  - New release: a single / EP / album released this week
  - Stream milestone: a track (or the whole catalog) crosses 1k, 5k, 10k, ...
  - New all-time monthly-listener high (vs state)
  - New playlist feature: Spotify added him to a playlist ("Featuring Asher")
  - New appearance: he shows up on someone else's release
  - Concert announced: a new upcoming show listed on his profile
  - Release announced: a pre-release countdown appears on his profile
  - Verified: his profile gets Spotify's verified badge

Playlists, appearances, and concerts are compared by ID against the previous
snapshot; on the very first run they're recorded as a baseline, not announced.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from accomplishment import Accomplishment
from sources import spotify

logger = logging.getLogger(__name__)

_TZ = ZoneInfo("America/Los_Angeles")
_FRIEND = "asher"
_SOURCE = "spotify"
_ARTIST_ID = "0mt9ovmSTm6oRJyZnr3EZS"

_MILESTONES = (1_000, 2_500, 5_000, 10_000, 25_000, 50_000, 100_000,
               250_000, 500_000, 1_000_000, 2_500_000, 5_000_000, 10_000_000)
_SNAPSHOTS_KEPT = 12


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _key(track_name: str) -> str:
    return track_name.strip().lower()


def _crossed(prev: int, now: int) -> list[int]:
    return [m for m in _MILESTONES if prev < m <= now]


def _fmt(n: int) -> str:
    return f"{n:,}"


def _make_snapshot(snap: dict) -> dict:
    return {
        "taken_at": datetime.now(_TZ).isoformat(timespec="seconds"),
        "monthly_listeners": snap["monthly_listeners"],
        "followers": snap["followers"],
        "verified": snap["verified"],
        "playcounts": {_key(t["name"]): t["playcount"] for t in snap["tracks"]},
        "featured_on": [p["id"] for p in snap["featured_on"]],
        "appears_on": [r["id"] for r in snap["appears_on"]],
        "concerts": [c["id"] for c in snap["concerts"]],
        "pre_release": (snap["pre_release"] or {}).get("id"),
    }


def _unseen(items: list[dict], prev: dict | None, field: str) -> list[dict]:
    """Items whose ID wasn't in the previous snapshot. Nothing on a first run."""
    if prev is None or field not in prev:
        return []
    seen = set(prev[field])
    return [i for i in items if i["id"] not in seen]


def _previous_snapshot(state: dict, week_key: str) -> tuple[str | None, dict | None]:
    earlier = sorted(k for k in state.get("snapshots", {}) if k < week_key)
    if not earlier:
        return None, None
    return earlier[-1], state["snapshots"][earlier[-1]]


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------

def _weekly_summary(snap: dict, prev: dict | None, prev_week: str | None,
                    since: datetime) -> list[Accomplishment]:
    tracks = snap["tracks"]
    total = sum(t["playcount"] for t in tracks)
    listeners = snap["monthly_listeners"]
    followers = snap["followers"]

    metrics = {
        "total_streams": total,
        "monthly_listeners": listeners,
        "followers": followers,
        "track_count": len(tracks),
        # Tracks Spotify reports as 0 (under 1,000 plays); total_streams is a floor.
        "tracks_under_1000": sum(1 for t in tracks if t["playcount"] < spotify.PLAYCOUNT_FLOOR),
        "tracks": [{"name": t["name"], "playcount": t["playcount"], "release": t["release"],
                    "duration_ms": t["duration_ms"], "url": t["url"]}
                   for t in tracks],
        "top_cities": snap["top_cities"],
        "world_rank": snap["world_rank"],
        "verified": snap["verified"],
        "labels": sorted({r["label"] for r in snap["releases"] if r["label"]}),
        "related_artists": snap["related_artists"],
        "featured_on": [p["name"] for p in snap["featured_on"]],
        "upcoming_concerts": len(snap["concerts"]),
        "image_url": snap["image_url"],
        "profile_url": f"https://open.spotify.com/artist/{snap['artist_id']}",
        "compared_to_week": prev_week,
    }

    if prev is None:
        top = tracks[0] if tracks else None
        summary = (
            f"{_fmt(total)} total Spotify streams across {len(tracks)} tracks"
            + (f" (top: {top['name']}, {_fmt(top['playcount'])})" if top else "")
            + f" · {_fmt(listeners)} monthly listeners · {_fmt(followers)} followers"
        )
        return [Accomplishment(_FRIEND, _SOURCE, "weekly_streams_summary", since, summary, metrics)]

    prev_counts: dict[str, int] = prev.get("playcounts", {})
    gains = []
    for t in tracks:
        before = prev_counts.get(_key(t["name"]))
        # Spotify hides counts under 1,000 as 0, so a track that was already
        # out at 0 has an unknown real gain once it shows up — skip it.
        if before == 0:
            continue
        gained = t["playcount"] - (before or 0)
        if gained > 0:
            gains.append((gained, t))
    gains.sort(key=lambda g: g[0], reverse=True)
    streams_gained = sum(g for g, _ in gains)
    listener_delta = listeners - prev.get("monthly_listeners", listeners)
    follower_delta = followers - prev.get("followers", followers)

    metrics.update({
        "streams_gained": streams_gained,
        "monthly_listeners_delta": listener_delta,
        "followers_delta": follower_delta,
        "track_gains": [{"name": t["name"], "gained": g} for g, t in gains],
    })

    parts = []
    if streams_gained:
        parts.append(f"+{_fmt(streams_gained)} streams (top: {gains[0][1]['name']} +{_fmt(gains[0][0])})")
    else:
        parts.append(f"{_fmt(total)} total streams")
    parts.append(f"{_fmt(listeners)} monthly listeners ({listener_delta:+,})")
    parts.append(f"{_fmt(followers)} followers ({follower_delta:+,})")
    return [Accomplishment(_FRIEND, _SOURCE, "weekly_streams_summary", since, " · ".join(parts), metrics)]


def _new_releases(snap: dict, since: datetime, until: datetime) -> list[Accomplishment]:
    results = []
    for rel in snap["releases"]:
        d = rel["release_date"]
        if d is None or not (since.date() <= d <= until.date()):
            continue
        kind = rel["type"].title() if rel["type"] != "EP" else "EP"
        ts = datetime.combine(d, datetime.min.time()).replace(tzinfo=_TZ)
        n = rel["track_count"]
        results.append(Accomplishment(
            friend=_FRIEND,
            source=_SOURCE,
            type="new_release",
            timestamp=ts,
            summary=f"New {kind} out: {rel['name']} ({n} track{'s' if n != 1 else ''})",
            metrics={
                "release": rel["name"],
                "release_type": rel["type"],
                "release_date": d.isoformat(),
                "track_count": n,
                "url": f"https://open.spotify.com/album/{rel['id']}",
            },
        ))
    return results


def _stream_milestones(snap: dict, prev: dict | None, since: datetime) -> list[Accomplishment]:
    # Without a previous snapshot we can't tell what was crossed this week.
    if prev is None:
        return []

    prev_counts: dict[str, int] = prev.get("playcounts", {})
    results = []
    for t in snap["tracks"]:
        before = prev_counts.get(_key(t["name"]), 0)
        hit = _crossed(before, t["playcount"])
        if not hit:
            continue
        results.append(Accomplishment(
            friend=_FRIEND,
            source=_SOURCE,
            type="new_stream_milestone",
            timestamp=since,
            summary=f"{t['name']} passed {_fmt(hit[-1])} streams ({_fmt(t['playcount'])} total)",
            metrics={
                "track": t["name"],
                "milestone": hit[-1],
                "playcount": t["playcount"],
                "previous_playcount": before,
                "url": f"https://open.spotify.com/track/{t['id']}",
            },
        ))

    total_now = sum(t["playcount"] for t in snap["tracks"])
    total_before = sum(prev_counts.values())
    hit = _crossed(total_before, total_now)
    if hit:
        results.append(Accomplishment(
            friend=_FRIEND,
            source=_SOURCE,
            type="new_stream_milestone",
            timestamp=since,
            summary=f"Catalog passed {_fmt(hit[-1])} total streams ({_fmt(total_now)})",
            metrics={
                "track": None,
                "milestone": hit[-1],
                "playcount": total_now,
                "previous_playcount": total_before,
            },
        ))
    return results


def _monthly_listener_high(snap: dict, state: dict, since: datetime) -> list[Accomplishment]:
    now = snap["monthly_listeners"]
    prev_best = state.get("best_monthly_listeners")
    if prev_best is None or now > prev_best:
        state["best_monthly_listeners"] = now
    if prev_best is None or now <= prev_best:
        return []
    return [Accomplishment(
        friend=_FRIEND,
        source=_SOURCE,
        type="new_monthly_listeners_high",
        timestamp=since,
        summary=f"New all-time high: {_fmt(now)} monthly listeners (was {_fmt(prev_best)})",
        metrics={"monthly_listeners": now, "previous_best": prev_best},
    )]


def _new_playlist_features(snap: dict, prev: dict | None, since: datetime) -> list[Accomplishment]:
    return [Accomplishment(
        friend=_FRIEND,
        source=_SOURCE,
        type="new_playlist_feature",
        timestamp=since,
        summary=f"Added to playlist: {p['name']}" + (f" (by {p['owner']})" if p["owner"] else ""),
        metrics={"playlist": p["name"], "owner": p["owner"], "url": p["url"]},
    ) for p in _unseen(snap["featured_on"], prev, "featured_on")]


def _new_appearances(snap: dict, prev: dict | None, since: datetime) -> list[Accomplishment]:
    results = []
    for r in _unseen(snap["appears_on"], prev, "appears_on"):
        by = ", ".join(r["artists"])
        results.append(Accomplishment(
            friend=_FRIEND,
            source=_SOURCE,
            type="new_appearance",
            timestamp=since,
            summary=f"Appears on {r['name']}" + (f" by {by}" if by else ""),
            metrics={
                "release": r["name"],
                "release_type": r["type"],
                "artists": r["artists"],
                "release_date": r["release_date"].isoformat() if r["release_date"] else None,
                "url": r["url"],
            },
        ))
    return results


def _new_concerts(snap: dict, prev: dict | None, since: datetime) -> list[Accomplishment]:
    results = []
    for c in _unseen(snap["concerts"], prev, "concerts"):
        where = ", ".join(x for x in (c["venue"], c["city"]) if x)
        day = c["start"][:10]
        results.append(Accomplishment(
            friend=_FRIEND,
            source=_SOURCE,
            type="concert_announced",
            timestamp=since,
            summary=f"Show announced: {c['title']}" + (f" @ {where}" if where else "") + (f" on {day}" if day else ""),
            metrics=dict(c),
        ))
    return results


def _release_announced(snap: dict, prev: dict | None, since: datetime) -> list[Accomplishment]:
    pre = snap["pre_release"]
    if not pre or prev is None or "pre_release" not in prev or prev["pre_release"] == pre["id"]:
        return []
    when = f" (out {pre['release_date'][:10]})" if pre["release_date"] else ""
    return [Accomplishment(
        friend=_FRIEND,
        source=_SOURCE,
        type="release_announced",
        timestamp=since,
        summary=f"Upcoming release announced: {pre['name'] or 'untitled'}{when}",
        metrics=dict(pre),
    )]


def _got_verified(snap: dict, prev: dict | None, since: datetime) -> list[Accomplishment]:
    if not snap["verified"] or prev is None or prev.get("verified", True):
        return []
    return [Accomplishment(
        friend=_FRIEND,
        source=_SOURCE,
        type="spotify_verified",
        timestamp=since,
        summary="Asher is now a verified artist on Spotify",
        metrics={"profile_url": f"https://open.spotify.com/artist/{snap['artist_id']}"},
    )]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def fetch(since: datetime, until: datetime, state: dict) -> list[Accomplishment]:
    snap = spotify.get_artist_snapshot(_ARTIST_ID)

    week_key = since.date().isoformat()
    prev_week, prev = _previous_snapshot(state, week_key)

    results: list[Accomplishment] = []
    results.extend(_weekly_summary(snap, prev, prev_week, since))
    results.extend(_new_releases(snap, since, until))
    results.extend(_stream_milestones(snap, prev, since))
    results.extend(_monthly_listener_high(snap, state, since))
    results.extend(_new_playlist_features(snap, prev, since))
    results.extend(_new_appearances(snap, prev, since))
    results.extend(_new_concerts(snap, prev, since))
    results.extend(_release_announced(snap, prev, since))
    results.extend(_got_verified(snap, prev, since))

    snapshots = state.setdefault("snapshots", {})
    snapshots[week_key] = _make_snapshot(snap)
    for old in sorted(snapshots)[:-_SNAPSHOTS_KEPT]:
        del snapshots[old]

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
    print(f"Fetching Asher's Spotify stats for {since.date()} → {until.date()}\n")
    state: dict = {}
    results = fetch(since, until, state)
    if not results:
        print("Nothing to report.")
    else:
        for r in results:
            print(f"  [{r.type}]\n  {r.summary}\n")
    print(f"state after: {state}")
