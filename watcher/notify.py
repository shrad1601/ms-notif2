import html
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import requests

from .core import NewItem, pending_weeks
from .fetch import MAKEUP_URL, RELIEF_URL
from .fit import Config, Fit
from .parse import Lesson

SGT = timezone(timedelta(hours=8))
MAX_LEN = 3800  # Telegram limit is 4096; leave room for the footer
QUIET_START, QUIET_END = 23, 7
STAR_MAX_RANK = 3  # see fit.rank: back-to-back, or continuing a class I've already bid on
e = html.escape


def is_quiet(now_utc: datetime) -> bool:
    hour = now_utc.replace(tzinfo=timezone.utc).astimezone(SGT).hour
    return hour >= QUIET_START or hour < QUIET_END


# ---- time formatting ---------------------------------------------------------------------
def _dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def _day(iso: str) -> str:
    dt = _dt(iso)
    return f"{dt:%a} {dt.day} {dt:%b}"


def _short_date(iso: str) -> str:
    dt = _dt(iso)
    return f"{dt.day} {dt:%b}"


def _hm(dt: datetime) -> str:
    h = dt.hour % 12 or 12
    return f"{h}" if dt.minute == 0 else f"{h}:{dt:%M}"


def _ap(dt: datetime) -> str:
    return "am" if dt.hour < 12 else "pm"


def _time(iso: str) -> str:
    dt = _dt(iso)
    return f"{_hm(dt)}{_ap(dt)}"


def _range(start: str, end: str) -> str:
    s, t = _dt(start), _dt(end)
    return f"{_hm(s)}–{_hm(t)}{_ap(t)}" if _ap(s) == _ap(t) else f"{_hm(s)}{_ap(s)}–{_hm(t)}{_ap(t)}"


def _when(start: str, end: str) -> str:
    return f"{_day(start)} {_range(start, end)}"


def _label(kind: str) -> str:
    return "Relief" if kind == "relief" else "Make-up"


# ---- grouping ----------------------------------------------------------------------------
@dataclass
class ClassAlert:
    """New (or still open) lessons of one class, plus how well they fit once the calendar is set up."""
    class_key: str
    items: list[NewItem]
    fits: list[Fit] | None = None
    rank: int | None = None
    taught: bool = False  # 🔁 covered this class before
    pending: list[str] = field(default_factory=list)  # weeks of this class I've already bid on

    @property
    def first(self) -> Lesson:
        return self.items[0].lesson

    @property
    def new_class(self) -> bool:
        return any(i.new_class for i in self.items)

    @property
    def starred(self) -> bool:
        return self.rank is not None and self.rank <= STAR_MAX_RANK

    @property
    def b2b(self) -> bool:
        return any(f.b2b for f in self.fits or [])

    def fitting(self) -> list[tuple[NewItem, Fit]]:
        return [(i, f) for i, f in zip(self.items, self.fits or []) if f.ok]


def group_new(items: list[NewItem], state: dict) -> list[ClassAlert]:
    groups: "OrderedDict[str, list[NewItem]]" = OrderedDict()
    for item in sorted(items, key=lambda i: (i.lesson.start, i.lesson.class_code)):
        groups.setdefault(item.lesson.class_key, []).append(item)
    return [ClassAlert(k, v, pending=pending_weeks(state, k)) for k, v in groups.items()]


def _icons(a: ClassAlert) -> str:
    return ("🔗" if a.b2b else "") + ("🔁" if a.taught else "")


def _weeks(a: ClassAlert) -> str:
    """' · 1 of 3 wks', ' · all 3 wks', or '' for single lessons."""
    n_all = len(a.items)
    if n_all == 1 or a.fits is None:
        return ""
    oks = a.fitting()
    if len(oks) == n_all:
        return f" · all {n_all} wks"
    extra = f" (also {', '.join(_short_date(i.lesson.start) for i, _ in oks[1:])})" if len(oks) > 1 else ""
    return f" · {len(oks)} of {n_all} wks{extra}"


def _notes(a: ClassAlert, fits: list[Fit], max_flags: int = 2) -> list[str]:
    lines = []
    for f in fits:
        for w in f.b2b_with[:1]:
            lines.append(f"🔗 {e(w)}")
            break
        if f.pairs:
            lines.append(f"🔗 pairs with {e(' / '.join(f.pairs[:2]))} here")
        if lines:
            break
    if a.pending:
        lines.append(f"✋ you've bid on other weeks ({e(', '.join(w.replace('MS Week', 'Wk') for w in a.pending))})")
    flags = list(dict.fromkeys(flag for f in fits for flag in f.flags))
    lines += [e(flag) for flag in flags[:max_flags]]
    return lines


# ---- new-class alerts --------------------------------------------------------------------
def _title(a: ClassAlert) -> str:
    first = a.first
    marks = ("⭐" if a.starred else "") + _icons(a)
    if a.new_class:
        return f"🆕{marks} <b>{_label(first.kind)}</b> · {e(first.class_name)}"
    n = len(a.items)
    return f"➕{marks} <b>{_label(first.kind)}</b> · {n} new week{'s' if n > 1 else ''} on {e(first.class_name)}"


def _lesson_line(item: NewItem, fit: Fit | None) -> list[str]:
    les = item.lesson
    week = f" ({e(les.week.replace('MS Week', 'Wk'))})" if les.week else ""
    tags = (" ↩️ back on bulletin" if item.relisted else "") + (" — you've bid" if les.status == "pending" else "")
    if fit is None:
        return [f"• {_when(les.start, les.end)}{week}{tags}"]
    if not fit.ok:
        return [f"❌ {_when(les.start, les.end)}{week}{tags} — {e(fit.reason or '')}"]
    lines = [f"✅ {_when(les.start, les.end)}{week}{tags}"]
    lines += [f"    🔗 {e(w)}" for w in fit.b2b_with[:1]]
    if fit.pairs and not fit.b2b_with:
        lines.append(f"    🔗 pairs with {e(' / '.join(fit.pairs[:2]))} here")
    lines += [f"    {e(flag)}" for flag in fit.flags[:2]]
    return lines


def class_block(a: ClassAlert) -> str:
    first = a.first
    lines = [_title(a), f"📍 {e(first.centre)} · <code>{e(first.class_code)}</code>"]
    if a.fits is not None and len(a.fits) > 1:
        n_fit, n = sum(f.ok for f in a.fits), len(a.fits)
        lines.append(f"<b>All {n} weeks fit</b>" if n_fit == n else f"<b>Fits {n_fit} of {n} weeks</b>")
    for i, item in enumerate(a.items):
        lines += _lesson_line(item, a.fits[i] if a.fits is not None else None)
    if first.closes:
        lines.append(f"Bidding closes {_day(first.closes)}, {_time(first.closes)}")
    if a.taught:
        lines.append("🔁 You've covered this class before")
    if a.pending and not a.new_class:
        lines.append(f"✋ You've already bid on: {e(', '.join(w.replace('MS Week', 'Wk') for w in a.pending))}")
    return "\n".join(lines)


def _digest_reason(a: ClassAlert) -> str:
    assert a.fits is not None
    n_fit, n = sum(f.ok for f in a.fits), len(a.fits)
    if n_fit:
        return ("fits, but standalone" + (f" ({n_fit} of {n} wks)" if n > 1 else "")
                + (" · 🔁 covered before" if a.taught else ""))
    reasons = {f.reason for f in a.fits}
    return next(iter(reasons)) if len(reasons) == 1 else "none of the weeks fit"


def digest_line(a: ClassAlert) -> str:
    first = a.first
    when = _when(first.start, first.end) if len(a.items) == 1 else \
        f"{_day(first.start)} – {_day(a.items[-1].lesson.start)} ({len(a.items)} wks)"
    icon = "🆕" if a.new_class else "➕"
    return f"{icon} {when}\n    {e(first.class_name)} · {e(first.centre)} — {e(_digest_reason(a))}"


def _footer(kinds: set[str]) -> str:
    links = []
    if "relief" in kinds:
        links.append(f'<a href="{e(RELIEF_URL)}">Relief bulletin</a>')
    if "makeup" in kinds:
        links.append(f'<a href="{e(MAKEUP_URL)}">Make-up bulletin</a>')
    return " · ".join(links)


def _pack(blocks: list[str], kinds: set[str], header: str = "", sep: str = "\n\n") -> list[str]:
    messages, current = [], header
    for block in blocks:
        candidate = f"{current}{sep}{block}" if current else block
        if len(candidate) > MAX_LEN and current and current != header:
            messages.append(current)
            current = block
        else:
            current = candidate
    if current and current != header:
        messages.append(current)
    footer = _footer(kinds)
    return [f"{m}\n\n{footer}" if footer else m for m in messages]


def format_alerts(alerts: list[ClassAlert]) -> list[str]:
    """⭐ classes get a message each (best first); everything else is packed into one summary."""
    if not alerts:
        return []
    if alerts[0].fits is None:
        return _pack([class_block(a) for a in alerts], {a.first.kind for a in alerts})
    starred = sorted((a for a in alerts if a.starred), key=lambda a: (a.rank, a.first.start))
    rest = sorted((a for a in alerts if not a.starred), key=lambda a: a.first.start)
    messages = []
    for a in starred:
        messages += _pack([class_block(a)], {a.first.kind})
    if rest:
        messages += _pack([digest_line(a) for a in rest], {a.first.kind for a in rest}, sep="\n",
                          header="<b>Also new (standalone, or doesn't fit):</b>")
    return messages


# ---- "open classes that fit" list --------------------------------------------------------
def _entry(a: ClassAlert, with_icons: bool) -> str:
    item, fit = a.fitting()[0]
    les = item.lesson
    icons = f"{_icons(a)} " if with_icons and _icons(a) else ""
    lines = [f"{icons}<b>{_when(les.start, les.end)}</b>",
             f"    {e(a.first.class_name)} · {e(les.centre)}{_weeks(a)}"]
    lines += [f"    {note}" for note in _notes(a, [f for _, f in a.fitting()])]
    return "\n".join(lines)


def _backlog_section(alerts: list[ClassAlert], title: str, kind: str) -> list[str]:
    fitting = [a for a in alerts if a.fitting()]
    by_date = lambda a: (a.fitting()[0][0].lesson.start, a.first.class_name)  # noqa: E731
    best = sorted((a for a in fitting if a.starred), key=lambda a: (a.rank, *by_date(a)))
    others = sorted((a for a in fitting if not a.starred), key=by_date)

    blocks = []
    if best:
        blocks.append("\n⭐ <b>Best bets</b> <i>(back-to-back, or a class you're already bidding on)</i>")
        blocks += [_entry(a, with_icons=True) for a in best]
    if others:
        blocks.append("\n<b>Others</b> <i>(fit, but standalone)</i>")
        blocks += [_entry(a, with_icons=True) for a in others]

    n = len(fitting)
    header = (f"📋 <b>{title}: {n} class{'es' if n != 1 else ''} fit</b> (not bid yet)\n"
              f"<i>🔗 back-to-back · 🔁 covered before · ✋ bid on other weeks · ⚠️ check</i>")
    return _pack(blocks, {kind}, header=header, sep="\n")


def format_backlog(alerts: list[ClassAlert], cfg: Config | None = None) -> list[str]:
    if not any(a.fitting() for a in alerts):
        return ["📋 Checked every open class against your calendar: nothing fits right now."]
    out = []
    for kind, title in (("relief", "Relief"), ("makeup", "Holiday Make-Up")):
        group = [a for a in alerts if a.first.kind == kind and a.fitting()]
        if group:
            out += _backlog_section(group, title, kind)
    return out


def format_new(items: list[NewItem], state: dict) -> list[str]:
    """Phase 1 format (no calendar): one block per class."""
    return format_alerts(group_new(items, state))


# ---- senders -----------------------------------------------------------------------------
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
