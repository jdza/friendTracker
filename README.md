# friendTracker

A weekly newsletter about what our friends are up to. Each person has one tracked source — Strava, chess.com, Mountain Project, Spotify, etc. Every Monday the pipeline pulls the previous week's activity, scores accomplishments by tier, and sends an HTML email to the group. A new all-time chess rating beats an average training week; a big climbing send beats routine mileage.

The pull and format stages are complete. Email delivery is built and waiting on the group Gmail account being allowed to send.

---

## How it works

### Pipeline stages

```
pull_data.py  →  newsletter.py  →  send_email.py
```

1. **Pull** — `pull_data.py` loads `friends.yaml`, calls `fetch()` on each friend's module, and writes `data/<week-start>.json`.
2. **Format** — `newsletter.py` reads the week's accomplishments, organizes them by friend and tier, and renders a short plain-text email to `data/<week-start>.txt` (default) or, with `--html`, an HTML version too.
3. **Send** — `send_email.py` delivers from theweeklyfriendship@gmail.com over Gmail SMTP to the recipient list. Delivery works (`python send_email.py --test`); hooking it up to the newsletter is not built yet.

### Layout

```
friends.yaml          config: one entry per friend, their source, credential keys
friends/<name>.py     one module per friend; each exports fetch(since, until, state)
sources/<platform>.py reusable API helpers (one per platform, friend-agnostic)
accomplishment.py     shared Accomplishment dataclass; everything downstream uses this
pull_data.py          entry point: loads config, runs fetch, writes data/<week>.json
seed_state.py         one-time historical lookback to populate state.json baselines
state.json            cross-week memory (best ratings, hardest grades, longest rides)
data/<week>.json      output from each run; gitignored
```

### Accomplishment

Every `fetch()` returns a list of `Accomplishment` objects:

```python
@dataclass
class Accomplishment:
    friend: str        # e.g. "steg"
    source: str        # e.g. "strava"
    type: str          # e.g. "weekly_ride_summary", "longest_activity"
    timestamp: datetime
    summary: str       # short human-readable string
    metrics: dict      # raw numbers for ranking/rendering
```

The newsletter stage only ever sees `Accomplishment` objects — it has no knowledge of Strava, chess.com, or Mountain Project.

### Tiers

Accomplishments are organized into three tiers within each friend's section:

| Tier | Types |
|------|-------|
| **1 — Records** | `new_*_rating_high`, `new_hardest_grade`, `new_longest_activity_ever`, `new_best_week_ever`, `new_monthly_listeners_high`, `new_stream_milestone`, `spotify_verified` |
| **2 — Highlights** | `hot_streak`, `upset_win`, `high_accuracy_game`, `clean_send`, `great_week`, `new_release`, `release_announced`, `new_playlist_feature`, `new_appearance`, `concert_announced` |
| **3 — The week** | `weekly_*_summary`, `weekly_*_record`, `longest_activity`, `weekly_streams_summary` |

### Weeks

Monday 00:00:00 → Sunday 23:59:59, America/Los_Angeles. `pull_data.py` defaults to the most recently completed week.

### State

`state.json` persists baselines needed for cross-week comparisons. Run `seed_state.py` once before the first weekly pull to populate it from full history — it scans all of Jake's chess.com stats, all of Lab's Mountain Project ticks, and all of Steg's Strava activities. After that, `pull_data.py` maintains it automatically each week.

---

## Setup

```bash
git clone https://github.com/jdza/friendTracker.git
cd friendTracker
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium   # headless browser for the Spotify scraper
cp .env.example .env
```

On Windows, activate with `.venv\Scripts\activate` and set `PYTHONUTF8=1` so the `→` in log output prints.

Then fill in `.env` with real credentials (see below). **Never commit `.env`** — it's gitignored.

### Credentials

`.env.example` documents the required keys:

```
STRAVA_CLIENT_ID=
STRAVA_CLIENT_SECRET=
STRAVA_REFRESH_TOKEN=
SENDER_EMAIL=theweeklyfriendship@gmail.com
GMAIL_APP_PASSWORD=
RECIPIENTS=
```

Strava credentials are tied to a specific app registered on Steg's account with `activity:read_all` scope. They're shared out-of-band. Without them, Steg's fetch will fail gracefully and the run will continue — a missing friend produces an error log line, not a crash.

Asher's Spotify data needs no credentials — it's scraped from the public web player. (Genres aren't tracked: Spotify strips them from the Web API for development-mode apps created after February 2026.)

### First run

After filling in `.env`, seed the historical baselines before pulling:

```bash
python seed_state.py   # one time only
python pull_data.py
```

---

## Running

### Test one friend

```bash
python -m friends.steg
python -m friends.jake
python -m friends.lab
python -m friends.asher
```

Prints last week's accomplishments directly. Use this when developing or debugging a friend module.

### Full pull

```bash
python pull_data.py                    # last completed week
python pull_data.py --week 2026-09-28  # backfill a specific week (must be a Monday)
```

Output goes to `data/<week-start>.json`. One friend failing doesn't stop the others.

### Newsletter

```bash
python newsletter.py                    # render last completed week → data/<week>.txt
python newsletter.py --week 2026-09-28  # a specific week (pull it first)
python newsletter.py --to me@gmail.com  # preview: email it to one address only
python newsletter.py --send             # email everyone in RECIPIENTS, one at a time
python newsletter.py --html --send      # HTML version instead (Gmail may filter it)
python send_email.py --test             # check email delivery on its own
```

Preview with `--to` (or open `data/<week>.txt`) before sending to everyone.

---

## Adding a friend

Steg is the worked example. To add someone new:

**1. Add a source helper if needed** (`sources/<platform>.py`)

`sources/strava.py` handles OAuth token refresh, paginated activity fetching, unit conversion, and error handling. If the new friend uses a platform that's already in `sources/`, skip this step.

**2. Create `friends/<name>.py`**

Must export:

```python
def fetch(since: datetime, until: datetime, state: dict) -> list[Accomplishment]:
    ...
```

- `since` / `until` are timezone-aware `datetime` objects (America/Los_Angeles).
- `state` is a dict you can read from and write to for cross-week memory.
- Return a list of `Accomplishment` objects. Empty list is fine if nothing happened.

Make it runnable standalone with `if __name__ == "__main__":` so you can test it without running the full pipeline. See `friends/steg.py` for the pattern.

**3. Add an entry to `friends.yaml`**

```yaml
- name: jake
  display_name: Jake
  source: chesscom
  module: friends.jake
  # add any credential keys here if needed
```

**4. Add any new env vars to `.env.example`**

Document the key names with empty values. Add the real values to your local `.env`.

That's it. `pull_data.py` discovers friends dynamically from the config — no changes needed there.

---

## Status

### Done

- **Pull stage complete** — all four friends pulling live data each week

- **Newsletter formatting** (`newsletter.py`) — plain text by default (the HTML version gets filtered by Gmail from the new group account; `--html` still renders it), one section per friend in `friends.yaml` order, items ordered Records → Highlights → The week, with links where the data has them. Subject line counts new records.

- **Jake / chess.com** — public API, no auth. Detects:
  - Weekly W/L/D record per time control (rapid, blitz, bullet)
  - Hot streak (longest consecutive-win run ≥3 in the week)
  - Great week (≥55% win rate, min 5 games)
  - Upset win (beat opponent rated 100+ higher)
  - High accuracy game (≥90% accuracy)
  - New all-time rating high per time control (vs seeded state)

- **Lab / Mountain Project** — public tick-export CSV. Detects:
  - Weekly climbing summary (tick count, clean sends, grade range, areas)
  - Clean sends (each Onsight, Flash, or Redpoint)
  - New all-time hardest clean send (vs seeded state; current baseline: 5.12c)

- **Steg / Strava** — OAuth with automatic token rotation. Detects:
  - Weekly summary per sport type (miles, elevation, time, count)
  - Longest activity of the week
  - New all-time longest single activity (vs seeded state; current: 100.3 mi ride)
  - New all-time best week by mileage (vs seeded state; current: 120.4 mi ride week)

- **Historical seeding** (`seed_state.py`) — scans full history for Jake, Lab, and Steg to establish true all-time baselines before the first weekly run (for Asher it just records current monthly listeners, since Spotify has no history)

- **Asher / Spotify** — scraped from the open.spotify.com web player (headless Chromium via Playwright), no login. Detects:
  - Weekly streams summary (plays gained per track, monthly listeners, followers, top cities, label, "Fans also like")
  - New release (single / EP / album released this week)
  - Release announced (pre-release countdown on his profile)
  - Stream milestones (a track or the whole catalog passing 1k, 5k, 10k, …)
  - New all-time monthly-listener high (vs state)
  - New playlist feature (Spotify playlists that feature him)
  - New appearance (on another artist's release)
  - Concert announced (new upcoming show on his profile)
  - Verified (gets Spotify's verified badge)

  Spotify only shows lifetime totals, so `state.json` keeps one snapshot per week and weekly numbers are the difference from the previous one. Tracks under 1,000 plays show as 0 on Spotify, so their weekly gain is unknown until they pass 1,000. Backfilling with `--week` reports current totals, not historical ones.

### Next

- **Email delivery** — working: plain-text issues go out one individually addressed email per person, 15 s apart. Needs a Gmail app password for the group account (Google won't accept the regular password over SMTP; create one at myaccount.google.com/apppasswords with 2-Step Verification on) and `RECIPIENTS` as a comma-separated list

### Later

- Weekly GitHub Actions schedule — needs Strava's rotating refresh token stored as a repo secret and updated by the workflow after each run
- More friends

---

## Rules

- **Never commit `.env` or tokens.** `.env` is in `.gitignore`. `.env.example` has placeholder keys only.
