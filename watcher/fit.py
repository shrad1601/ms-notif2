"""Does a bulletin lesson fit my schedule? Hard rules + calendar clashes + soft flags."""
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from .parse import Lesson

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


@dataclass
class Event:
    title: str
    start: datetime  # naive SGT
    end: datetime
    location: str = ""
    all_day: bool = False
    free: bool = False
    source: str = "calendar"  # "calendar" or "dash"


@dataclass
class Config:
    too_far: list[str] = field(default_factory=list)
    latest_end: dict[str, str] = field(default_factory=dict)  # {"Tue": "17:00"}
    soft_keywords: list[str] = field(default_factory=list)  # event titles that only flag, never block
    nearby_minutes: int = 30
    back_to_back_weekday: int = 30
    back_to_back_weekend: int = 45
    calendar_ids: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "Config":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass
class Fit:
    ok: bool
    reason: str | None = None  # why it doesn't fit
    flags: list[str] = field(default_factory=list)  # ⚠️ things to judge yourself
    back_to_back: bool = False  # next to something already in my schedule at the same centre
    b2b_with: list[str] = field(default_factory=list)  # e.g. "after your P3 English (10:00am–12:00pm)"
    pairs: list[str] = field(default_factory=list)  # other open lessons right next to this one, same centre

    @property
    def b2b(self) -> bool:
        return self.ok and (self.back_to_back or bool(self.pairs))


def b2b_limit(start: datetime, kind: str, cfg: "Config") -> int:
    """Max gap (minutes) that still counts as back-to-back. Make-ups use the longer gap (10am then 12:45pm)."""
    if start.weekday() >= 5 or kind == "makeup":
        return cfg.back_to_back_weekend
    return cfg.back_to_back_weekday


def _hm(dt: datetime) -> str:
    h = dt.hour % 12 or 12
    return f"{h}" if dt.minute == 0 else f"{h}:{dt:%M}"


def _ap(dt: datetime) -> str:
    return "am" if dt.hour < 12 else "pm"


def _fmt_time(dt: datetime) -> str:
    return f"{_hm(dt)}{_ap(dt)}"


def _fmt_day(dt: datetime) -> str:
    return f"{dt.day} {dt:%b}"


def _span(ev: Event) -> str:
    if ev.all_day:
        return "all day"
    s, t = ev.start, ev.end
    return f"{_hm(s)}–{_hm(t)}{_ap(t)}" if _ap(s) == _ap(t) else f"{_fmt_time(s)}–{_fmt_time(t)}"


_PLACE_NOISE = {"mind", "stretcher", "cc", "mall", "shopping", "centre", "center", "learning", "the", "@", "-"}


def _place_words(text: str) -> set[str]:
    words = {w.strip("(),.") for w in text.lower().split()}
    return {w for w in words if w and w not in _PLACE_NOISE}


def _same_place(location: str, centre: str) -> bool:
    """'Sengkang CC' and 'Mind Stretcher Sengkang' are the same place."""
    loc, cen = _place_words(location), _place_words(centre)
    return bool(loc and cen) and (cen <= loc or loc <= cen)


def is_trip(ev: Event) -> bool:
    return ev.all_day or ev.end - ev.start >= timedelta(hours=24)


def blocks_time(ev: Event) -> bool:
    """Free/busy only matters for ordinary timed events: all-day and multi-day events (trips, 'Away')
    always block, because Google marks all-day events as Free by default."""
    return is_trip(ev) or not ev.free


def _trip_days(ev: Event) -> tuple[datetime, datetime]:
    """A trip blocks every day it touches, including the day you leave and the day you're back."""
    start = datetime.combine(ev.start.date(), time.min)
    last_day = (ev.end - timedelta(seconds=1)).date() if ev.all_day else ev.end.date()
    return start, datetime.combine(last_day, time.min) + timedelta(days=1)


def _where(location: str) -> str:
    loc = location.strip()
    if not loc:
        return ""
    return " (online)" if loc.lower() == "online" else f" @ {loc}"


def _minutes(td: timedelta) -> int:
    return int(td.total_seconds() // 60)


def evaluate(lesson: Lesson, events: list[Event], cfg: Config, pending: list[Lesson] = ()) -> Fit:
    start = datetime.fromisoformat(lesson.start)
    end = datetime.fromisoformat(lesson.end)
    weekday = WEEKDAYS[start.weekday()]

    centre = lesson.centre.lower()
    for far in cfg.too_far:
        if far.lower() in centre:
            return Fit(False, f"too far ({lesson.centre})")

    latest = cfg.latest_end.get(weekday)
    if latest:
        limit = time.fromisoformat(latest)
        if end.time() > limit or end.date() > start.date():
            return Fit(False, f"ends {_fmt_time(end)}, after your {weekday} limit")

    fit = Fit(True)
    limit = b2b_limit(start, lesson.kind, cfg)
    for ev in sorted(events, key=lambda e: e.start):
        if not blocks_time(ev):
            continue
        soft = any(k.lower() in ev.title.lower() for k in cfg.soft_keywords)
        if is_trip(ev):
            t_start, t_end = _trip_days(ev)
            if t_start < end and t_end > start:
                if soft:
                    fit.flags.append(f"⚠️ clashes with {ev.title}")
                    continue
                last = t_end - timedelta(days=1)
                when = "all day" if last.date() == t_start.date() else f"{_fmt_day(t_start)}–{_fmt_day(last)}"
                return Fit(False, f"{ev.title} ({when})")
            continue
        if ev.start < end and ev.end > start:
            if soft:
                fit.flags.append(f"⚠️ clashes with {ev.title} ({_span(ev)})")
                continue
            return Fit(False, f"clash: {ev.title} ({_span(ev)})")
        if ev.end <= start:
            gap, side = start - ev.end, "after"
        else:
            gap, side = ev.start - end, "before"
        mins = _minutes(gap)
        if _same_place(ev.location, lesson.centre):
            if mins <= limit:
                fit.back_to_back = True
                fit.b2b_with.append(f"{side} your {ev.title} ({_span(ev)})")
            continue
        if mins > cfg.nearby_minutes:
            continue
        gap_text = f"right {side}" if mins == 0 else f"{mins} min {side}"
        fit.flags.append(f"⚠️ {gap_text} {ev.title}{_where(ev.location)}")

    for other in pending:
        if other.key == lesson.key:
            continue
        o_start, o_end = datetime.fromisoformat(other.start), datetime.fromisoformat(other.end)
        if o_start < end and o_end > start:
            return Fit(False, f"overlaps your pending bid ({other.class_name}, {_fmt_time(o_start)})")
    return fit


def rank(fits: list[Fit], taught: bool = False, continuing: bool = False) -> int:
    """I only want classes that sit next to something else.

    1 = back-to-back and (covered before or already bidding on other weeks)
    2 = back-to-back
    3 = standalone, but I've already bid on other weeks of this class
    4 = fits but standalone (listed, no ⭐)
    5 = doesn't fit
    """
    if not any(f.ok for f in fits):
        return 5
    if any(f.b2b for f in fits):
        return 1 if (taught or continuing) else 2
    return 3 if continuing else 4
