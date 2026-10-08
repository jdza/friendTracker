# friendTracker

A weekly newsletter about what our friends are up to. Each person has one tracked source — Strava, chess.com, Mountain Project, etc. Every Monday the pipeline pulls the previous week's activity, scores accomplishments against each other, and sends a newsletter. A new all-time chess rating beats an average training week; a big climbing send beats routine mileage.

The project is in the **pull stage**. Ranking and delivery don't exist yet.

---

## How it works

### Pipeline stages

```
pull_data.py  →  rank (todo)  →  format + send (todo)
```

1. **Pull** — `pull_data.py` loads `friends.yaml`, calls `fetch()` on each friend's module, and writes `data/<week-start>.json`.
2. **Rank** — score each `Accomplishment` against others. Not built yet.
3. **Format + send** — render the newsletter and deliver it. Not built yet.

### Layout

```
friends.yaml          config: one entry per friend, their source, credential keys
friends/<name>.py     one module per friend; each exports fetch(since, until, state)
sources/<platform>.py reusable API helpers (one per platform, friend-agnostic)
accomplishment.py     shared Accomplishment dataclass; everything downstream uses this
pull_data.py          entry point: loads config, runs fetch, writes data/<week>.json
state.json            cross-week memory (last-seen ticks, best ratings, etc.)
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
    metrics: dict      # raw numbers for ranking later
```

The ranking stage only ever sees `Accomplishment` objects — it has no knowledge of Strava, chess.com, or Mountain Project.

### Weeks

Monday 00:00:00 → Sunday 23:59:59, America/Los_Angeles. `pull_data.py` defaults to the most recently completed week.

### State

`state.json` persists things that need to be compared across weeks: a chess player's previous best rating, the last Mountain Project tick seen, etc. It's a dict keyed by friend name. Each friend module receives its own slice as the `state` argument to `fetch()` and can write back to it.

Strava doesn't need state right now — weekly summaries are computed fresh from the API each run.

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
```

Strava credentials are tied to a specific app registered on Steg's account with `activity:read_all` scope. They're shared out-of-band. Without them, Steg's fetch will fail gracefully and the run will continue — a missing friend produces an error log line, not a crash.

---

## Running

### Test one friend

```bash
python -m friends.steg
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

- **Steg / Strava** — OAuth set up via an app registered on Steg's Strava account (`activity:read_all` scope). Token refresh is automatic; if Strava rotates the refresh token, it's written back to `.env` atomically. Each week produces:
  - One summary per sport type (total miles, elevation, moving time, activity count)
  - The longest single activity of the week

### Next

- **Jake / chess.com** — public API, no auth required. Track new wins and new all-time-high ratings per time control.
- **Lab / Mountain Project** — public tick-export CSV from his profile. Track new ticks with grades.

### Later

- Ranking stage — scoring accomplishments across different sports and sources
- Newsletter formatting and delivery (email? group chat? TBD)
- Weekly GitHub Actions schedule — needs Strava's rotating refresh token stored as a repo secret and updated by the workflow after each run
- More friends

### Open questions

- How to score across sports (150 ride miles vs. 150 run miles vs. a 5.12 send)
- Which chess time controls count for Jake (blitz, rapid, classical?)
- How the newsletter gets delivered

---

## Rules

- **Never commit `.env` or tokens.** `.env` is in `.gitignore`. `.env.example` has placeholder keys only.
- **Strava's API terms prohibit feeding Strava data into AI/LLM prompts.** The entire Strava path — data fetching, processing, and any future newsletter text that uses Strava data — must stay plain code with no LLM calls.
