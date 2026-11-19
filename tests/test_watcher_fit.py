from datetime import date, datetime, timedelta

from fakepages import schedule_week
from test_watcher import T0, WK, MemoryStore, Site, run, texts
from watcher.dash_schedule import week_url
from watcher.fetch import FixtureClient
from watcher.fit import Config, Event
from watcher.main import Watcher
from watcher.notify import Recorder

CFG = Config(too_far=["Far Centre"], latest_end={"Tue": "17:00", "Thu": "17:00", "Fri": "19:15"},
             calendar_ids=["me@example.com"])
MONDAY = date(2026, 11, 16)  # week of T0 in SGT


class FakeCalendar:
    def __init__(self, events=(), fail=False):
        self.items = list(events)
        self.fail = fail

    def events(self, start, end):
        if self.fail:
            raise RuntimeError("calendar down")
        return [e for e in self.items if e.start < end and e.end > start]


class ScheduleSite(Site):
    def __init__(self):
        super().__init__()
        self.schedule = {}  # monday -> entries

    def client(self):
        base = super().client()
        pages = dict(base.pages)
        for w in range(-1, 10):
            monday = MONDAY + timedelta(weeks=w)
            pages[week_url(monday)] = schedule_week(monday, self.schedule.get(monday, []))
        return FixtureClient(pages)


def make(site, calendar):
    store = MemoryStore()
    rec = Recorder()
    return Watcher(site.client(), store, rec, CFG, calendar), rec, store


def ev(title, start, end, location=""):
    return Event(title, datetime.fromisoformat(start), datetime.fromisoformat(end), location)


def test_baseline_sends_backlog_once():
    site = ScheduleSite()
    w, rec, store = make(site, FakeCalendar())
    run(w, site, T0)
    msgs = texts(rec)
    assert "now watching" in msgs[0]
    assert "📋" in msgs[1] and "4 open classes fit" in msgs[1]
    assert store.state["backlog_sent"]
    run(w, site, T0 + timedelta(minutes=61))
    assert len(rec.sent) == 2


def test_new_class_with_one_clashing_week_is_starred():
    site = ScheduleSite()
    cal = FakeCalendar([ev("Other job", "2026-12-19T14:30", "2026-12-19T16:30", "Some Mall")])
    w, rec, _ = make(site, cal)
    run(w, site, T0)
    site.relief[2].append(("CC-1", "P6 Maths", "Centre E",
                           [("MS Week 7", WK.format(d="19"), False), ("MS Week 8", WK.format(d="26"), False)]))
    run(w, site, T0 + timedelta(minutes=6))
    msg = texts(rec)[-1]
    assert msg.startswith("🆕⭐ <b>Relief</b> · P6 Maths")
    assert "Fits 1 of 2 weeks" in msg
    assert "❌ Sat 19 Dec" in msg and "clash: Other job" in msg
    assert "✅ Sat 26 Dec" in msg


def test_too_far_goes_to_digest():
    site = ScheduleSite()
    w, rec, _ = make(site, FakeCalendar())
    run(w, site, T0)
    site.relief[2].append(("FC-1", "P4 Science", "Far Centre", [("MS Week 7", WK.format(d="19"), False)]))
    run(w, site, T0 + timedelta(minutes=6))
    msg = texts(rec)[-1]
    assert msg.startswith("<b>Also new, but doesn't fit:</b>")
    assert "P4 Science · Far Centre" in msg and "too far" in msg


def test_class_covered_before_gets_repeat_mark_and_top_rank():
    site = ScheduleSite()
    site.schedule[MONDAY] = [(5, "2:00 PM - 4:00 PM", "P6 Maths", "Centre E", True)]
    w, rec, store = make(site, FakeCalendar())
    run(w, site, T0)
    assert len(store.state["dash"]["taught"]) == 1
    site.relief[2].append(("CC-1", "P6 Maths", "Centre E", [("MS Week 7", WK.format(d="19"), False)]))
    run(w, site, T0 + timedelta(minutes=6))
    msg = texts(rec)[-1]
    assert msg.startswith("🆕⭐🔁") and "covered this class before" in msg


def test_assigned_dash_lesson_counts_as_busy():
    site = ScheduleSite()
    site.schedule[MONDAY + timedelta(weeks=4)] = [(5, "2:00 PM - 4:00 PM", "P3 English", "Centre Z", False)]
    w, rec, _ = make(site, FakeCalendar())
    run(w, site, T0)
    site.relief[2].append(("CC-1", "P6 Maths", "Centre E", [("MS Week 7", WK.format(d="19"), False)]))
    run(w, site, T0 + timedelta(minutes=6))
    msg = texts(rec)[-1]
    assert "doesn't fit" in msg and "clash: P3 English (Dash)" in msg


def test_calendar_failure_still_alerts_without_stars():
    site = ScheduleSite()
    cal = FakeCalendar()
    w, rec, store = make(site, cal)
    run(w, site, T0)
    cal.fail = True
    site.relief[2].append(("CC-1", "P6 Maths", "Centre E", [("MS Week 7", WK.format(d="19"), False)]))
    run(w, site, T0 + timedelta(minutes=6))
    msgs = texts(rec)
    assert any("couldn't read your Google Calendar" in m for m in msgs)
    alert = msgs[-1]
    assert alert.startswith("🆕 <b>Relief</b> · P6 Maths") and "⭐" not in alert
