import html
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import requests

from .core import NewItem, pending_weeks
from .fetch import MAKEUP_URL, RELIEF_URL
from .fit import Config, Fit, b2b_limit
from .parse import Lesson

SGT = timezone(timedelta(hours=8))
MAX_LEN = 3800  # Telegram limit is 4096; leave room for the footer
QUIET_START, QUIET_END = 23, 7
e = html.escape


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


def _week(les: Lesson) -> str:
    return f" ({e(les.week.replace('MS Week', 'Wk'))})" if les.week else ""


def _label(kind: str) -> str:
    return "Relief" if kind == "relief" else "Make-up"


@dataclass
class ClassAlert:
    """New lessons of one class, plus (once the calendar is set up) how well they fit."""
    class_key: str
    items: list[NewItem]
    fits: list[Fit] | None = None
    rank: int | None = None
    taught: bool = False
    pending: list[str] = field(default_factory=list)

    @property
    def first(self) -> Lesson:
        return self.items[0].lesson

    @property
    def new_class(self) -> bool:
        return any(i.new_class for i in self.items)

    @property
    def starred(self) -> bool:
        return self.rank is not None and self.rank <= 4


def group_new(items: list[NewItem], state: dict) -> list[ClassAlert]:
    groups: "OrderedDict[str, list[NewItem]]" = OrderedDict()
    for item in sorted(items, key=lambda i: (i.lesson.start, i.lesson.class_code)):
        groups.setdefault(item.lesson.class_key, []).append(item)
    return [ClassAlert(k, v, pending=pending_weeks(state, k)) for k, v in groups.items()]


def _title(a: ClassAlert) -> str:
    first = a.first
    marks = ("⭐" if a.starred else "") + ("🔁" if a.taught else "")
    if a.new_class:
        return f"🆕{marks} <b>{_label(first.kind)}</b> · {e(first.class_name)}"
    n = len(a.items)
    return f"➕{marks} <b>{_label(first.kind)}</b> · {n} new week{'s' if n > 1 else ''} on {e(first.class_name)}"


def _lesson_line(item: NewItem, fit: Fit | None) -> list[str]:
    les = item.lesson
    tags = (" ↩️ back on bulletin" if item.relisted else "") + (" — you've bid" if les.status == "pending" else "")
    if fit is None:
        return [f"• {_when(les.start, les.end)}{_week(les)}{tags}"]
    if not fit.ok:
        return [f"❌ {_when(les.start, les.end)}{_week(les)}{tags} — {e(fit.reason or '')}"]
    lines = [f"✅ {_when(les.start, les.end)}{_week(les)}{' 🔗' if fit.back_to_back else ''}{tags}"]
    lines += [f"    ↳ 🔗 back-to-back {e(w)}" for w in fit.b2b_with[:1]]
    lines += [f"    ↳ <i>{e(flag)}</i>" for flag in fit.flags]
    return lines


def class_block(a: ClassAlert) -> str:
    first = a.first
    lines = [_title(a), f"📍 {e(first.centre)} · <code>{e(first.class_code)}</code>"]
    if a.fits is not None:
        n_fit, n = sum(f.ok for f in a.fits), len(a.fits)
        if n == 1:
            lines.append("<b>Fits your schedule</b>" if n_fit else "<b>Doesn't fit</b>")
        else:
            lines.append(f"<b>All {n} weeks fit</b>" if n_fit == n else f"<b>Fits {n_fit} of {n} weeks</b>")
    for i, item in enumerate(a.items):
        lines += _lesson_line(item, a.fits[i] if a.fits is not None else None)
    if first.closes:
        lines.append(f"Bidding closes {_day(first.closes)}, {_time(first.closes)}")
    if a.taught:
        lines.append("🔁 You've covered this class before")
    if a.pending and not a.new_class:
        lines.append(f"🔁 You've already bid on: {e(', '.join(w.replace('MS Week', 'Wk') for w in a.pending))}")
    return "\n".join(lines)


def _not_fit_reason(a: ClassAlert) -> str:
    assert a.fits is not None
    n_fit, n = sum(f.ok for f in a.fits), len(a.fits)
    if n_fit:
        return f"fits {n_fit} of {n} weeks"
    reasons = {f.reason for f in a.fits}
    return next(iter(reasons)) if len(reasons) == 1 else "none of the weeks fit"


def digest_line(a: ClassAlert) -> str:
    first = a.first
    when = _day(first.start) if len(a.items) == 1 else f"{_day(first.start)} – {_day(a.items[-1].lesson.start)} ({len(a.items)})"
    icon = "🆕" if a.new_class else "➕"
    return (f"• {icon} {_label(first.kind)} · {e(first.class_name)} · {e(first.centre)} · {when}"
            f" — {e(_not_fit_reason(a))}")


def _footer(kinds: set[str]) -> str:
    links = []
    if "relief" in kinds:
        links.append(f'<a href="{e(RELIEF_URL)}">Relief bulletin</a>')
    if "makeup" in kinds:
        links.append(f'<a href="{e(MAKEUP_URL)}">Make-up bulletin</a>')
    return " · ".join(links)


def _pack(blocks: list[str], kinds: set[str], header: str = "") -> list[str]:
    messages, current = [], header
    for block in blocks:
        candidate = f"{current}\n\n{block}" if current else block
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
    """⭐ classes get a message each (best first); everything else is packed into a digest."""
    if not alerts:
        return []
    if alerts[0].fits is None:
        return _pack([class_block(a) for a in alerts], {a.first.kind for a in alerts})
    starred = sorted((a for a in alerts if a.starred),
                     key=lambda a: (a.rank, not any(f.back_to_back for f in a.fits or []), a.first.start))
    rest = [a for a in alerts if not a.starred]
    messages = []
    for a in starred:
        messages += _pack([class_block(a)], {a.first.kind})
    if rest:
        lines = [digest_line(a) for a in rest]
        messages += _pack(["\n".join(lines[i:i + 25]) for i in range(0, len(lines), 25)],
                          {a.first.kind for a in rest}, header="<b>Also new, but doesn't fit:</b>")
    return messages


def format_new(items: list[NewItem], state: dict) -> list[str]:
    """Phase 1 format (no calendar): one block per class."""
    return format_alerts(group_new(items, state))


def _range(start: str, end: str) -> str:
    s, t = datetime.fromisoformat(start), datetime.fromisoformat(end)

    def hm(dt: datetime) -> str:
        return f"{dt.hour % 12 or 12}:{dt:%M}"

    def ap(dt: datetime) -> str:
        return "am" if dt.hour < 12 else "pm"

    return f"{hm(s)}–{hm(t)}{ap(t)}" if ap(s) == ap(t) else f"{hm(s)}{ap(s)}–{hm(t)}{ap(t)}"


def _short_date(iso: str) -> str:
    dt = datetime.fromisoformat(iso)
    return f"{dt.day} {dt:%b}"


@dataclass
class _Entry:
    alert: ClassAlert
    oks: list[tuple[Lesson, Fit]]  # weeks that fit, soonest first
    linked: bool = False  # back-to-back with another open lesson in this list

    @property
    def lesson(self) -> Lesson:
        return self.oks[0][0]

    @property
    def fit(self) -> Fit:
        return self.oks[0][1]


def _backlog_section(alerts: list[ClassAlert], title: str, kind: str, cfg: Config) -> list[str]:
    entries = []
    for a in alerts:
        oks = [(i.lesson, f) for i, f in zip(a.items, a.fits or []) if f.ok]
        if oks:
            entries.append(_Entry(a, oks))

    by_day: dict[str, dict[str, list[_Entry]]] = {}
    for en in sorted(entries, key=lambda en: (en.lesson.start[:10], en.lesson.centre, en.lesson.start,
                                              en.lesson.class_name)):
        by_day.setdefault(en.lesson.start[:10], {}).setdefault(en.lesson.centre, []).append(en)

    b2b_lines = []
    for day, centres in by_day.items():
        for centre, group in centres.items():
            times = set()
            for x in group:
                for y in group:
                    xs, xe = datetime.fromisoformat(x.lesson.start), datetime.fromisoformat(x.lesson.end)
                    ys = datetime.fromisoformat(y.lesson.start)
                    if xe <= ys and (ys - xe).total_seconds() / 60 <= b2b_limit(xs, kind, cfg):
                        x.linked = y.linked = True
                        times |= {x.lesson.start, y.lesson.start}
            if times:
                n = sum(en.linked for en in group)
                chain = " → ".join(_time(t) for t in sorted(times))
                b2b_lines.append(f"• {_day(day)} · {e(centre)}: {chain} ({n} classes to pick from)")
            for en in group:
                if en.fit.b2b_with:
                    b2b_lines.append(f"• {_day(day)} · {e(centre)}: {_time(en.lesson.start)} "
                                     f"{e(en.alert.first.class_name)}, {e(en.fit.b2b_with[0])}")

    blocks = []
    if b2b_lines:
        blocks.append("<b>🔗 Back-to-back options</b>\n" + "\n".join(b2b_lines))
    for day, centres in by_day.items():
        lines = [f"<b>{_day(day)}</b>"]
        for centre, group in centres.items():
            lines.append(f"📍 {e(centre)}")
            for en in group:
                a = en.alert
                marks = "🔁 " if (a.taught or a.pending) else ""
                weeks = ""
                if len(a.items) > 1:
                    n_ok, n_all = len(en.oks), len(a.items)
                    weeks = f" · all {n_all} weeks" if n_ok == n_all else f" · {n_ok} of {n_all} weeks"
                    if len(en.oks) > 1:
                        weeks += f" (also {', '.join(_short_date(les.start) for les, _ in en.oks[1:])})"
                b2b = " 🔗" if (en.linked or en.fit.back_to_back) else ""
                lines.append(f"• {_range(en.lesson.start, en.lesson.end)} · {marks}{e(a.first.class_name)}{weeks}{b2b}")
                flags = list(dict.fromkeys(flag for _, f in en.oks for flag in f.flags))
                lines += [f"    ↳ <i>{e(flag)}</i>" for flag in flags]
        blocks.append("\n".join(lines))

    n = len(entries)
    header = (f"📋 <b>{title}: {n} class{'es' if n != 1 else ''} fit your schedule</b>\n"
              f"<i>Open and not bid on yet · 🔗 back-to-back · 🔁 covered before · ⚠️ check the note</i>")
    return _pack(blocks, {kind}, header=header)


def format_backlog(alerts: list[ClassAlert], cfg: Config) -> list[str]:
    starred = [a for a in alerts if a.starred]
    if not starred:
        return ["📋 Checked every open class against your calendar: nothing fits right now."]
    out = []
    for kind, title in (("relief", "Relief"), ("makeup", "Holiday Make-Up")):
        group = [a for a in starred if a.first.kind == kind]
        if group:
            out += _backlog_section(group, title, kind, cfg)
    return out


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
