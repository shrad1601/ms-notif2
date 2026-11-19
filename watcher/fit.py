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
    nearby_minutes: int = 60
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
    back_to_back: bool = False
    b2b_with: list[str] = field(default_factory=list)  # e.g. "after P4 English (Dash) (12:45–2:45pm)"


def b2b_limit(start: datetime, kind: str, cfg: "Config") -> int:
    """Max gap (minutes) that still counts as back-to-back. Make-ups use the longer gap (10am then 12:45pm)."""
    if start.weekday() >= 5 or kind == "makeup":
        return cfg.back_to_back_weekend
    return cfg.back_to_back_weekday


def _fmt_time(dt: datetime) -> str:
    h = dt.hour % 12 or 12
    return f"{h}:{dt:%M}{'am' if dt.hour < 12 else 'pm'}"


def _span(ev: Event) -> str:
    if ev.all_day:
        return "all day"
    return f"{_fmt_time(ev.start)}–{_fmt_time(ev.end)}"


_PLACE_NOISE = {"mind", "stretcher", "cc", "mall", "shopping", "centre", "center", "learning", "the", "@", "-"}


def _place_words(text: str) -> set[str]:
    words = {w.strip("(),.") for w in text.lower().split()}
    return {w for w in words if w and w not in _PLACE_NOISE}


def _same_place(location: str, centre: str) -> bool:
    """'Sengkang CC' and 'Mind Stretcher Sengkang' are the same place."""
    loc, cen = _place_words(location), _place_words(centre)
    return bool(loc and cen) and (cen <= loc or loc <= cen)


def blocks_time(ev: Event) -> bool:
    """Free/busy only matters for ordinary timed events: all-day and multi-day events (trips, 'Away')
    always block, because Google marks all-day events as Free by default."""
    if ev.all_day or ev.end - ev.start >= timedelta(hours=24):
        return True
    return not ev.free


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
        if ev.start < end and ev.end > start:
            if soft:
                fit.flags.append(f"⚠️ clashes with {ev.title} ({_span(ev)})")
                continue
            return Fit(False, f"clash: {ev.title} ({_span(ev)})")
        if ev.all_day:
            continue
        if ev.end <= start:
            gap, side = start - ev.end, "after"
        else:
            gap, side = ev.start - end, "before"
        mins = _minutes(gap)
        if mins > cfg.nearby_minutes:
            continue
        if _same_place(ev.location, lesson.centre):
            if mins <= limit:
                fit.back_to_back = True
                fit.b2b_with.append(f"{side} {ev.title} ({_span(ev)})")
            continue
        where = f" @ {ev.location}" if ev.location else ""
        fit.flags.append(f"⚠️ {mins} min {side} {ev.title}{where}")

    for other in pending:
        if other.key == lesson.key:
            continue
        o_start, o_end = datetime.fromisoformat(other.start), datetime.fromisoformat(other.end)
        if o_start < end and o_end > start:
            return Fit(False, f"overlaps your pending bid ({other.class_name}, {_fmt_time(o_start)})")
    return fit


def rank(fits: list[Fit], taught: bool) -> int:
    """1 = 🔁 + all fit, 2 = 🔁 + some fit, 3 = all fit, 4 = at least half fit, 5 = the rest (no ⭐)."""
    n_fit = sum(f.ok for f in fits)
    if n_fit == 0:
        return 5
    if taught:
        return 1 if n_fit == len(fits) else 2
    if n_fit == len(fits):
        return 3
    return 4 if n_fit * 2 >= len(fits) else 5
