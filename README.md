# friendTracker

A weekly newsletter about what our friends are up to. Each person has one tracked source — Strava, chess.com, Mountain Project, etc. Every Monday the pipeline pulls the previous week's activity, scores accomplishments by tier, and sends an HTML email to the group. A new all-time chess rating beats an average training week; a big climbing send beats routine mileage.

The pull stage is complete. Newsletter formatting and delivery are next.

---

## How it works

### Pipeline stages

```
pull_data.py  →  newsletter.py  →  send_email.py
```

1. **Pull** — `pull_data.py` loads `friends.yaml`, calls `fetch()` on each friend's module, and writes `data/<week-start>.json`.
2. **Format** — `newsletter.py` reads the week's accomplishments, organizes them by friend and tier, and renders HTML + plain text. Not built yet.
3. **Send** — `send_email.py` delivers via SendGrid to the recipient list. Not built yet.

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
| **1 — Records** | `new_*_rating_high`, `new_hardest_grade`, `new_longest_activity_ever`, `new_best_week_ever` |
| **2 — Highlights** | `hot_streak`, `upset_win`, `high_accuracy_game`, `clean_send`, `great_week` |
| **3 — The week** | `weekly_*_summary`, `weekly_*_record`, `longest_activity` |

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
cp .env.example .env
```

Then fill in `.env` with real credentials (see below). **Never commit `.env`** — it's gitignored.

### Credentials

`.env.example` documents the required keys:

```
STRAVA_CLIENT_ID=
STRAVA_CLIENT_SECRET=
STRAVA_REFRESH_TOKEN=
SENDGRID_API_KEY=
```

Strava credentials are tied to a specific app registered on Steg's account with `activity:read_all` scope. They're shared out-of-band. Without them, Steg's fetch will fail gracefully and the run will continue — a missing friend produces an error log line, not a crash.

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
```

Prints last week's accomplishments directly. Use this when developing or debugging a friend module.

### Full pull

```bash
python pull_data.py                    # last completed week
python pull_data.py --week 2026-09-28  # backfill a specific week (must be a Monday)
```

Output goes to `data/<week-start>.json`. One friend failing doesn't stop the others.

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

- **Pull stage complete** — all three friends pulling live data each week

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

- **Historical seeding** (`seed_state.py`) — scans full history for all three friends to establish true all-time baselines before the first weekly run

### Next

- **Newsletter formatting** — render accomplishments into HTML + plain text email, organized by friend with tier-based ordering (records → highlights → weekly facts)
- **Email delivery** — SendGrid integration, recipient list in `.env`

### Later

- Weekly GitHub Actions schedule — needs Strava's rotating refresh token stored as a repo secret and updated by the workflow after each run
- More friends

---

## Rules

- **Never commit `.env` or tokens.** `.env` is in `.gitignore`. `.env.example` has placeholder keys only.
