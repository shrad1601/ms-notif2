from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from .parse import Lesson

REAPPEAR_AFTER = timedelta(hours=24)  # gone (per a complete scan) this long, then back = alert again
FORGET_GONE_AFTER = timedelta(days=7)
FORGET_UNSEEN_AFTER = timedelta(days=60)
CLASS_FORGET_AFTER = timedelta(days=30)


@dataclass
class NewItem:
    lesson: Lesson
    new_class: bool  # True = the whole class is new; False = new week(s) on a known class
    relisted: bool = False


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def apply_scan(state: dict, kind: str, lessons: list[Lesson], complete: bool, now: datetime) -> list[NewItem]:
    """Merge a full scan into state and return the lessons worth alerting about. `now` is naive UTC."""
    baseline = not state["baseline_done"][kind]
    seen: dict = state["lessons"]
    classes: dict = state["classes"]
    now_s = _iso(now)
    new: list[NewItem] = []
    known_classes = set(classes)
    found_keys = set()

    for lesson in lessons:
        found_keys.add(lesson.key)
        prev = seen.get(lesson.key)
        gone_since = prev.get("gone_since") if prev else None
        relisted = gone_since is not None and _parse(gone_since) <= now - REAPPEAR_AFTER
        if prev is None or relisted:
            if not baseline:
                new.append(NewItem(lesson, new_class=lesson.class_key not in known_classes, relisted=relisted))
            record = asdict(lesson)
            record.pop("extra", None)
            record["first_seen"] = prev["first_seen"] if prev else now_s
            seen[lesson.key] = record
        record = seen[lesson.key]
        record["last_seen"] = now_s
        record["status"] = lesson.status
        record["request_id"] = lesson.request_id or record.get("request_id")
        record.pop("gone_since", None)
        cls = classes.get(lesson.class_key)
        if cls is None:
            classes[lesson.class_key] = {"first_seen": now_s, "last_seen": now_s}
        else:
            cls["last_seen"] = now_s

    if complete:
        for key, rec in seen.items():
            if rec["kind"] == kind and key not in found_keys and "gone_since" not in rec:
                rec["gone_since"] = now_s
        state["last_full"][kind] = now_s
        if baseline:
            state["baseline_done"][kind] = True
    _prune(state, now)
    return new


def _prune(state: dict, now: datetime) -> None:
    seen = state["lessons"]
    for key in [k for k, v in seen.items()
                if ("gone_since" in v and _parse(v["gone_since"]) < now - FORGET_GONE_AFTER)
                or _parse(v["last_seen"]) < now - FORGET_UNSEEN_AFTER]:
        del seen[key]
    classes = state["classes"]
    live = {f'{v["kind"]}|{v["class_code"]}' for v in seen.values()}
    for key in [k for k, v in classes.items()
                if k not in live and _parse(v["last_seen"]) < now - CLASS_FORGET_AFTER]:
        del classes[key]


def pending_weeks(state: dict, class_key: str) -> list[str]:
    """Labels of lessons in this class that I've already bid on (as last seen on the bulletin)."""
    out = []
    for rec in state["lessons"].values():
        if (f'{rec["kind"]}|{rec["class_code"]}' == class_key and rec.get("status") == "pending"
                and "gone_since" not in rec):
            out.append(rec.get("week") or rec["start"])
    return sorted(out)


def due(state: dict, kind: str, every: timedelta, now: datetime) -> bool:
    last = state["last_full"].get(kind)
    return last is None or _parse(last) <= now - every
