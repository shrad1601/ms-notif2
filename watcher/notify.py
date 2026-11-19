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


# ---- messages with buttons ---------------------------------------------------------------
@dataclass
class Button:
    label: str
    action: str  # "bid" or "skip"


@dataclass
class ButtonRow:
    payload: dict  # what a tap acts on: {"kind", "class_key", "keys", "label"}
    buttons: list[Button]


@dataclass
class Message:
    text: str
    rows: list[ButtonRow] = field(default_factory=list)


def _bid_payload(a: ClassAlert) -> dict:
    return {"kind": a.first.kind, "class_key": a.class_key,
            "keys": [i.lesson.key for i, _ in a.fitting()],
            "label": f"{a.first.class_name} · {a.first.centre}"}


def _alert_row(a: ClassAlert) -> ButtonRow:
    n, n_all = len(a.fitting()), len(a.items)
    text = "✅ Bid" if n == 1 else (f"✅ Bid all {n} wks" if n == n_all else f"✅ Bid {n} fitting wks")
    return ButtonRow(_bid_payload(a), [Button(text, "bid"), Button("Skip", "skip")])


def _list_row(a: ClassAlert) -> ButtonRow:
    les = a.fitting()[0][0].lesson
    name = a.first.class_name if len(a.first.class_name) <= 24 else a.first.class_name[:23] + "…"
    centre = les.centre if len(les.centre) <= 16 else les.centre[:15] + "…"
    weeks = f" ({len(a.fitting())} wks)" if len(a.fitting()) > 1 else ""
    return ButtonRow(_bid_payload(a), [Button(f"Bid · {_short_date(les.start)} {_time(les.start)} · {name}"
                                              f" · {centre}{weeks}", "bid")])


def _pack(blocks: list, kinds: set[str], header: str = "", sep: str = "\n\n") -> list[Message]:
    """Join blocks into messages under Telegram's size limit. A block is text, or (text, button rows)."""
    messages: list[Message] = []
    current, rows = header, []
    for block in blocks:
        text, block_rows = block if isinstance(block, tuple) else (block, [])
        candidate = f"{current}{sep}{text}" if current else text
        if len(candidate) > MAX_LEN and current and current != header:
            messages.append(Message(current, rows))
            current, rows = text, list(block_rows)
        else:
            current = candidate
            rows += block_rows
    if current and current != header:
        messages.append(Message(current, rows))
    footer = _footer(kinds)
    for m in messages:
        if footer:
            m.text = f"{m.text}\n\n{footer}"
    return messages


def format_alerts(alerts: list[ClassAlert]) -> list[Message]:
    """⭐ classes get a message each (best first) with a Bid button; everything else is packed into one summary."""
    if not alerts:
        return []
    if alerts[0].fits is None:
        return _pack([class_block(a) for a in alerts], {a.first.kind for a in alerts})
    starred = sorted((a for a in alerts if a.starred), key=lambda a: (a.rank, a.first.start))
    rest = sorted((a for a in alerts if not a.starred), key=lambda a: a.first.start)
    messages = []
    for a in starred:
        messages += _pack([(class_block(a), [_alert_row(a)])], {a.first.kind})
    if rest:
        messages += _pack([digest_line(a) for a in rest], {a.first.kind for a in rest}, sep="\n",
                          header="<b>Also new (standalone, or doesn't fit):</b>")
    return messages


# ---- "open classes that fit" list --------------------------------------------------------
LEGEND = "<i>🔗 back-to-back · 🔁 covered before · ✋ already bid other weeks · ⚠️ check</i>"


def _class_label(a: ClassAlert) -> str:
    """'P4 Science (Achiever) 🔁 · 2 of 3 wks, also 7 Dec'"""
    marks = ("🔁" if a.taught else "") + ("✋" if a.pending else "")
    label = e(a.first.class_name) + (f" {marks}" if marks else "")
    n_all, oks = len(a.items), a.fitting()
    if n_all > 1:
        if len(oks) == n_all:
            label += " · both wks" if n_all == 2 else f" · all {n_all} wks"
        else:
            label += f" · {len(oks)} of {n_all} wks"
        if len(oks) > 1:
            label += ", also " + ", ".join(_short_date(i.lesson.start) for i, _ in oks[1:])
    return label


def _day_block(day_iso: str, centres: dict, show_b2b: bool) -> tuple[str, list[ClassAlert]]:
    lines = [f"<b>{_day(day_iso)}</b>"]
    shown: list[ClassAlert] = []
    for centre, rows in centres.items():
        head = f"📍 {e(centre)}"
        if show_b2b:
            existing = next((w for _, f, _ in rows for w in f.b2b_with), None)
            paired = sorted({les.start for les, f, _ in rows if f.pairs})
            partner = next((p for _, f, _ in rows for p in f.pairs), None)
            if existing:
                head += f" · 🔗 {e(existing)}"
            elif len(paired) > 1:
                head += " · 🔗 " + " → ".join(_time(t) for t in paired)
            elif partner:
                head += f" · 🔗 next to {e(partner)}"
        lines.append(head)
        slots: "OrderedDict[tuple[str, str], list[tuple[Fit, ClassAlert]]]" = OrderedDict()
        for les, f, a in sorted(rows, key=lambda r: (r[0].start, r[2].first.class_name)):
            slots.setdefault((les.start, les.end), []).append((f, a))
        for (start, end), members in slots.items():
            lines.append(f"• {_range(start, end)}: " + " · ".join(_class_label(a) for _, a in members))
            flags = list(dict.fromkeys(flag for f, _ in members for flag in f.flags))
            lines += [f"    {e(flag)}" for flag in flags[:2]]
            shown += [a for _, a in members]
    return "\n".join(lines), shown


def _calendar_message(alerts: list[ClassAlert], title: str, subtitle: str, kind: str, best: bool) -> list[Message]:
    """Month → day → centre → time slot, with a blank line between days. Best bets get a Bid button per class."""
    months: "OrderedDict[str, OrderedDict[str, OrderedDict[str, list]]]" = OrderedDict()
    rows = sorted(((a.fitting()[0][0].lesson, a.fitting()[0][1], a) for a in alerts),
                  key=lambda r: (r[0].start[:10], r[0].centre, r[0].start))
    for les, f, a in rows:
        (months.setdefault(les.start[:7], OrderedDict())
               .setdefault(les.start[:10], OrderedDict())
               .setdefault(les.centre, []).append((les, f, a)))

    blocks: list = []
    for month, days in months.items():
        blocks.append(f"📅 <b>{datetime.fromisoformat(month + '-01').strftime('%B %Y').upper()}</b>")
        for day, centres in days.items():
            text, shown = _day_block(day, centres, show_b2b=best)
            blocks.append((text, [_list_row(a) for a in shown] if best else []))
    header = f"📋 <b>{title}</b>\n<i>{subtitle}</i>\n{LEGEND}"
    return _pack(blocks, {kind}, header=header)


def format_backlog(alerts: list[ClassAlert], cfg: Config | None = None) -> list[Message]:
    fitting = [a for a in alerts if a.fitting()]
    if not fitting:
        return [Message("📋 Checked every open class against your calendar: nothing fits right now.")]
    out = []
    for kind, title in (("relief", "RELIEF"), ("makeup", "HOLIDAY MAKE-UP")):
        group = [a for a in fitting if a.first.kind == kind]
        best = [a for a in group if a.starred]
        others = [a for a in group if not a.starred]
        if best:
            n = len(best)
            out += _calendar_message(best, f"{title} · ⭐ BEST BETS",
                                     f"{n} class{'es' if n != 1 else ''}: back-to-back, or already bidding "
                                     f"on other weeks. Tap a button to bid.", kind, best=True)
        if others:
            n = len(others)
            out += _calendar_message(others, f"{title} · OTHERS",
                                     f"{n} class{'es that fit' if n != 1 else ' that fits'}, but standalone",
                                     kind, best=False)
    return out


def format_new(items: list[NewItem], state: dict) -> list[Message]:
    """Phase 1 format (no calendar): one block per class."""
    return format_alerts(group_new(items, state))


# ---- senders -----------------------------------------------------------------------------
class Telegram:
    def __init__(self, token: str, chat_id: str):
        self.api = f"https://api.telegram.org/bot{token}/"
        self.chat_id = str(chat_id)

    def _call(self, method: str, payload: dict, http_timeout: int = 20):
        try:
            r = requests.post(self.api + method, json=payload, timeout=http_timeout)
        except requests.RequestException as exc:
            print(f"telegram {method}: {exc.__class__.__name__}")
            return None
        if r.status_code != 200:
            print(f"telegram {method}: HTTP {r.status_code}")
            return None
        return r.json().get("result")

    def send(self, text: str, silent: bool = False, keyboard: list | None = None,
             reply_to: int | None = None) -> int | None:
        payload = {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML",
                   "disable_web_page_preview": True, "disable_notification": silent}
        if keyboard:
            payload["reply_markup"] = {"inline_keyboard": keyboard}
        if reply_to:
            payload["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
        result = self._call("sendMessage", payload)
        return result.get("message_id") if result else None

    def edit_keyboard(self, message_id: int, keyboard: list) -> None:
        self._call("editMessageReplyMarkup", {"chat_id": self.chat_id, "message_id": message_id,
                                              "reply_markup": {"inline_keyboard": keyboard}})

    def edit_text(self, message_id: int, text: str) -> bool:
        return self._call("editMessageText", {"chat_id": self.chat_id, "message_id": message_id, "text": text,
                                              "parse_mode": "HTML", "disable_web_page_preview": True}) is not None

    def answer(self, callback_id: str, text: str) -> None:
        self._call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})

    def updates(self, offset: int | None, timeout: int) -> list[dict]:
        """Long-poll: Telegram holds the request open for up to `timeout` seconds waiting for taps."""
        payload = {"timeout": timeout, "allowed_updates": ["callback_query"]}
        if offset is not None:
            payload["offset"] = offset
        return self._call("getUpdates", payload, http_timeout=timeout + 15) or []


class Recorder:
    """Stands in for Telegram in dry runs and tests. Only prints messages if asked (logs are public)."""

    def __init__(self, show: bool = False, chat_id: str = "me"):
        self.sent: list[tuple[str, bool, list | None, int | None]] = []
        self.edits: list[tuple[int, list]] = []
        self.answers: list[tuple[str, str]] = []
        self.queue: list[dict] = []  # scripted updates for tests
        self.show = show
        self.chat_id = chat_id

    def send(self, text: str, silent: bool = False, keyboard: list | None = None,
             reply_to: int | None = None) -> int:
        self.sent.append((text, silent, keyboard, reply_to))
        if self.show:
            print(f"--- message (silent={silent}) ---\n{text}\n")
        return len(self.sent)

    def edit_keyboard(self, message_id: int, keyboard: list) -> None:
        self.edits.append((message_id, keyboard))

    def edit_text(self, message_id: int, text: str) -> bool:
        _, silent, keyboard, reply_to = self.sent[message_id - 1]
        self.sent[message_id - 1] = (text, silent, keyboard, reply_to)
        return True

    def answer(self, callback_id: str, text: str) -> None:
        self.answers.append((callback_id, text))

    def updates(self, offset: int | None, timeout: int) -> list[dict]:
        out = [u for u in self.queue if offset is None or u["update_id"] >= offset]
        self.queue = []
        return out
