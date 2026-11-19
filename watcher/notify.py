import html
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

import requests

from .core import NewItem, pending_weeks
from .fetch import MAKEUP_URL, RELIEF_URL

SGT = timezone(timedelta(hours=8))
MAX_LEN = 3800  # Telegram limit is 4096; leave room for the footer
QUIET_START, QUIET_END = 23, 7


def is_quiet(now_utc: datetime) -> bool:
    hour = now_utc.replace(tzinfo=timezone.utc).astimezone(SGT).hour
    return hour >= QUIET_START or hour < QUIET_END


def _day(iso: str) -> str:
    dt = datetime.fromisoformat(iso)
    return f"{dt:%a} {dt.day} {dt:%b}"


def _time(iso: str) -> str:
    dt = datetime.fromisoformat(iso)
    h = dt.hour % 12 or 12
    return f"{h}:{dt:%M}{'am' if dt.hour < 12 else 'pm'}"


def _when(start: str, end: str) -> str:
    return f"{_day(start)}, {_time(start)}–{_time(end)}"


def format_new(items: list[NewItem], state: dict) -> list[str]:
    """One block per class, soonest first, packed into as few messages as fit."""
    groups: "OrderedDict[str, list[NewItem]]" = OrderedDict()
    for item in sorted(items, key=lambda i: (i.lesson.start, i.lesson.class_code)):
        groups.setdefault(item.lesson.class_key, []).append(item)

    blocks = []
    kinds = set()
    for class_key, group in groups.items():
        first = group[0].lesson
        kinds.add(first.kind)
        label = "Relief" if first.kind == "relief" else "Make-up"
        e = html.escape
        if any(i.new_class for i in group):
            title = f"🆕 <b>{label}</b> · {e(first.class_name)}"
        else:
            n = len(group)
            title = f"➕ <b>{label}</b> · {n} new week{'s' if n > 1 else ''} on {e(first.class_name)}"
        lines = [title, f"📍 {e(first.centre)} · <code>{e(first.class_code)}</code>"]
        for item in group:
            les = item.lesson
            week = f" ({e(les.week.replace('MS Week', 'Wk'))})" if les.week else ""
            tag = " ↩️ back on bulletin" if item.relisted else ""
            mine = " — you've bid" if les.status == "pending" else ""
            lines.append(f"• {_when(les.start, les.end)}{week}{tag}{mine}")
        if first.closes:
            lines.append(f"Bidding closes {_day(first.closes)}, {_time(first.closes)}")
        if not any(i.new_class for i in group):
            mine = pending_weeks(state, class_key)
            if mine:
                lines.append(f"🔁 You've already bid on: {e(', '.join(w.replace('MS Week', 'Wk') for w in mine))}")
        blocks.append("\n".join(lines))

    footer = []
    if "relief" in kinds:
        footer.append(f'<a href="{html.escape(RELIEF_URL)}">Relief bulletin</a>')
    if "makeup" in kinds:
        footer.append(f'<a href="{html.escape(MAKEUP_URL)}">Make-up bulletin</a>')
    footer_text = " · ".join(footer)

    messages, current = [], ""
    for block in blocks:
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) > MAX_LEN and current:
            messages.append(current)
            current = block
        else:
            current = candidate
    if current:
        messages.append(current)
    return [f"{m}\n\n{footer_text}" for m in messages]


class Telegram:
    def __init__(self, token: str, chat_id: str):
        self.url = f"https://api.telegram.org/bot{token}/sendMessage"
        self.chat_id = chat_id

    def send(self, text: str, silent: bool = False) -> bool:
        try:
            r = requests.post(self.url, timeout=20, data={
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
                "disable_notification": "true" if silent else "false",
            })
            if r.status_code != 200:
                print(f"telegram: HTTP {r.status_code}")
            return r.status_code == 200
        except requests.RequestException as exc:
            print(f"telegram: {exc.__class__.__name__}")
            return False


class Recorder:
    """Stands in for Telegram in dry runs. Keeps messages; only prints them if asked (logs are public)."""

    def __init__(self, show: bool = False):
        self.sent: list[tuple[str, bool]] = []
        self.show = show

    def send(self, text: str, silent: bool = False) -> bool:
        self.sent.append((text, silent))
        if self.show:
            print(f"--- message (silent={silent}) ---\n{text}\n")
        return True
