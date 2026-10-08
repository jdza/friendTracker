"""
newsletter.py — render a week's accomplishments into the newsletter email.

Reads data/<week>.json (written by pull_data.py) and friends.yaml, groups
accomplishments by friend in config order, and orders each friend's items by
tier: records → highlights → the week. Writes data/<week>.html and
data/<week>.txt for previewing.

Usage:
  python newsletter.py                    # last completed week
  python newsletter.py --week 2026-09-28  # a specific week (must be a Monday)
  python newsletter.py --send             # render, then email via send_email.py
"""

import argparse
import html
import json
import logging
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml

from pull_data import last_completed_monday

logger = logging.getLogger(__name__)

_DATA_DIR = Path("data")
_TITLE = "The Weekly Friendship"

# Tier 1 — Records, 2 — Highlights, 3 — The week. First match wins; anything
# unlisted lands in tier 3 so a new detector never disappears from the email.
_TIER_PATTERNS = [
    (1, r"new_.*_rating_high|new_hardest_grade|new_longest_activity_ever|new_best_week_ever"
        r"|new_monthly_listeners_high|new_stream_milestone|spotify_verified"),
    (2, r"hot_streak|upset_win|high_accuracy_game|clean_send|great_week|new_release"
        r"|release_announced|new_playlist_feature|new_appearance|concert_announced"),
]
_TIER_LABELS = {1: "Records", 2: "Highlights", 3: "The week"}
_TIER_COLORS = {1: "#b45309", 2: "#1d4ed8", 3: "#4b5563"}


def tier_of(acc_type: str) -> int:
    for tier, pattern in _TIER_PATTERNS:
        if re.fullmatch(pattern, acc_type):
            return tier
    return 3


def _link(acc: dict) -> str | None:
    m = acc.get("metrics") or {}
    return m.get("url") or m.get("profile_url")


def _week_label(monday: date) -> str:
    sunday = monday + timedelta(days=6)
    if monday.month == sunday.month:
        return f"{monday:%b} {monday.day} – {sunday.day}, {sunday.year}"
    return f"{monday:%b} {monday.day} – {sunday:%b} {sunday.day}, {sunday.year}"


def build_sections(accomplishments: list[dict], friends: list[dict]) -> list[dict]:
    """[{display_name, tiers: {tier: [acc, ...]}}] in friends.yaml order."""
    by_friend: dict[str, list[dict]] = {}
    for acc in accomplishments:
        by_friend.setdefault(acc["friend"], []).append(acc)

    sections = []
    for f in friends:
        tiers: dict[int, list[dict]] = {}
        for acc in sorted(by_friend.get(f["name"], []), key=lambda a: a["timestamp"]):
            tiers.setdefault(tier_of(acc["type"]), []).append(acc)
        sections.append({"display_name": f.get("display_name", f["name"]), "tiers": dict(sorted(tiers.items()))})
    return sections


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_text(monday: date, sections: list[dict]) -> str:
    lines = [_TITLE, _week_label(monday), ""]
    for s in sections:
        lines.append(s["display_name"].upper())
        if not s["tiers"]:
            lines.append("  Quiet week.")
        for tier, accs in s["tiers"].items():
            lines.append(f"  {_TIER_LABELS[tier]}")
            for acc in accs:
                lines.append(f"    - {acc['summary']}")
                if _link(acc):
                    lines.append(f"      {_link(acc)}")
        lines.append("")
    return "\n".join(lines)


def render_html(monday: date, sections: list[dict]) -> str:
    # Inline styles only: most email clients strip <style> blocks.
    e = html.escape
    parts = [
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{e(_TITLE)}</title></head>",
        '<body style="margin:0;padding:0;background:#f3f4f6;">',
        '<div style="max-width:600px;margin:0 auto;padding:24px 16px;'
        'font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#111827;">',
        f'<h1 style="margin:0;font-size:26px;">{e(_TITLE)}</h1>',
        f'<p style="margin:4px 0 24px;color:#6b7280;font-size:14px;">{e(_week_label(monday))}</p>',
    ]
    for s in sections:
        parts.append('<div style="background:#ffffff;border-radius:10px;padding:18px 20px;margin-bottom:16px;">')
        parts.append(f'<h2 style="margin:0 0 10px;font-size:20px;">{e(s["display_name"])}</h2>')
        if not s["tiers"]:
            parts.append('<p style="margin:0;color:#6b7280;font-size:15px;">Quiet week.</p>')
        for tier, accs in s["tiers"].items():
            parts.append(
                f'<p style="margin:12px 0 4px;font-size:12px;font-weight:700;letter-spacing:.06em;'
                f'text-transform:uppercase;color:{_TIER_COLORS[tier]};">{_TIER_LABELS[tier]}</p>'
            )
            parts.append('<ul style="margin:0;padding-left:20px;">')
            for acc in accs:
                text = e(acc["summary"])
                url = _link(acc)
                if url:
                    text = f'<a href="{e(url)}" style="color:#111827;">{text}</a>'
                weight = "600" if tier == 1 else "400"
                parts.append(f'<li style="margin:4px 0;font-size:15px;line-height:1.45;font-weight:{weight};">{text}</li>')
            parts.append("</ul>")
        parts.append("</div>")
    parts.append('<p style="margin:24px 0 0;color:#9ca3af;font-size:12px;">Sent by friendTracker.</p>')
    parts.append("</div></body></html>")
    return "\n".join(parts)


def render(monday: date) -> tuple[str, str, str]:
    """(subject, text, html) for the week starting on monday."""
    data_path = _DATA_DIR / f"{monday.isoformat()}.json"
    if not data_path.exists():
        raise FileNotFoundError(f"{data_path} not found — run: python pull_data.py --week {monday.isoformat()}")
    accomplishments = json.loads(data_path.read_text(encoding="utf-8"))
    with open("friends.yaml", encoding="utf-8") as f:
        friends = yaml.safe_load(f)["friends"]

    sections = build_sections(accomplishments, friends)
    records = sum(len(s["tiers"].get(1, [])) for s in sections)
    subject = f"{_TITLE} · {_week_label(monday)}"
    if records:
        subject += f" · {records} new record{'s' if records != 1 else ''}"
    return subject, render_text(monday, sections), render_html(monday, sections)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--week", metavar="YYYY-MM-DD",
                        help="Monday of the week to render (default: last completed week)")
    parser.add_argument("--send", action="store_true", help="email the newsletter to RECIPIENTS")
    args = parser.parse_args()

    if args.week:
        monday = date.fromisoformat(args.week)
        if monday.weekday() != 0:
            sys.exit(f"--week must be a Monday; {args.week} is a {monday.strftime('%A')}")
    else:
        monday = last_completed_monday()

    try:
        subject, text, body_html = render(monday)
    except FileNotFoundError as exc:
        sys.exit(str(exc))

    html_path = _DATA_DIR / f"{monday.isoformat()}.html"
    html_path.write_text(body_html, encoding="utf-8")
    (_DATA_DIR / f"{monday.isoformat()}.txt").write_text(text, encoding="utf-8")
    logger.info("Wrote %s (subject: %s)", html_path, subject)

    if args.send:
        import send_email
        send_email.send(subject, text, body_html)


if __name__ == "__main__":
    main()
