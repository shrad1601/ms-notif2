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
DIGEST = "<b>Also new (standalone, or doesn't fit):</b>"


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
    # AA-1 has a pending bid on another week -> best bet; the rest are standalone
    assert [m.split("\n", 1)[0] for m in msgs[1:]] == [
        "📋 <b>RELIEF · ⭐ BEST BETS</b>",
        "📋 <b>RELIEF · OTHERS</b>",
        "📋 <b>HOLIDAY MAKE-UP · OTHERS</b>",
    ]
    assert "<i>1 class that fits, but standalone</i>" in msgs[2]
    assert store.state["backlog_sent"]
    run(w, site, T0 + timedelta(minutes=61))
    assert len(rec.sent) == 4


def test_standalone_new_class_goes_to_summary():
    site = ScheduleSite()
    w, rec, _ = make(site, FakeCalendar())
    run(w, site, T0)
    site.relief[2].append(("CC-1", "P6 Maths", "Centre E", [("MS Week 7", WK.format(d="19"), False)]))
    run(w, site, T0 + timedelta(minutes=6))
    msg = texts(rec)[-1]
    assert msg.startswith(DIGEST)
    assert "P6 Maths · Centre E — fits, but standalone" in msg


def test_new_class_next_to_my_class_is_starred():
    site = ScheduleSite()
    # my existing (Dash) class at the same centre on Sat 26 Dec, 11:45am-1:45pm
    site.schedule[MONDAY + timedelta(weeks=5)] = [(5, "11:45 AM - 1:45 PM", "P3 English", "Centre E", False)]
    cal = FakeCalendar([ev("Other job", "2026-12-19T14:30", "2026-12-19T16:30", "Some Mall")])
    w, rec, _ = make(site, cal)
    run(w, site, T0)
    site.relief[2].append(("CC-1", "P6 Maths", "Centre E",
                           [("MS Week 7", WK.format(d="19"), False), ("MS Week 8", WK.format(d="26"), False)]))
    run(w, site, T0 + timedelta(minutes=6))
    msg = texts(rec)[-1]
    assert msg.startswith("🆕⭐🔗 <b>Relief</b> · P6 Maths")
    assert "Fits 1 of 2 weeks" in msg
    assert "❌ Sat 19 Dec 2–4pm (Wk 7) — clash: Other job" in msg
    assert "✅ Sat 26 Dec 2–4pm (Wk 8)\n    🔗 after your P3 English (11:45am–1:45pm)" in msg


def test_too_far_goes_to_digest():
    site = ScheduleSite()
    w, rec, _ = make(site, FakeCalendar())
    run(w, site, T0)
    site.relief[2].append(("FC-1", "P4 Science", "Far Centre", [("MS Week 7", WK.format(d="19"), False)]))
    run(w, site, T0 + timedelta(minutes=6))
    msg = texts(rec)[-1]
    assert msg.startswith(DIGEST)
    assert "P4 Science · Far Centre — too far (Far Centre)" in msg


def test_covered_before_is_tagged_but_standalone_is_not_starred():
    site = ScheduleSite()
    site.schedule[MONDAY] = [(5, "2:00 PM - 4:00 PM", "P6 Maths", "Centre E", True)]
    w, rec, store = make(site, FakeCalendar())
    run(w, site, T0)
    assert len(store.state["dash"]["taught"]) == 1
    site.relief[2].append(("CC-1", "P6 Maths", "Centre E", [("MS Week 7", WK.format(d="19"), False)]))
    run(w, site, T0 + timedelta(minutes=6))
    msg = texts(rec)[-1]
    assert msg.startswith(DIGEST) and "🔁 covered before" in msg


def test_bidding_on_other_weeks_is_not_covered_before():
    site = ScheduleSite()
    w, rec, _ = make(site, FakeCalendar())
    run(w, site, T0)
    best = next(t for t in texts(rec) if t.startswith("📋 <b>RELIEF · ⭐ BEST BETS"))
    # AA-1 has a pending bid on Wk 4: shown as ✋, never as 🔁, and listed as a best bet
    assert "• 2–4pm: P5 English ✋" in best
    body = best.split("⚠️ check</i>", 1)[1]
    assert "🔁" not in body


def test_assigned_dash_lesson_counts_as_busy():
    site = ScheduleSite()
    site.schedule[MONDAY + timedelta(weeks=4)] = [(5, "2:00 PM - 4:00 PM", "P3 English", "Centre Z", False)]
    w, rec, _ = make(site, FakeCalendar())
    run(w, site, T0)
    site.relief[2].append(("CC-1", "P6 Maths", "Centre E", [("MS Week 7", WK.format(d="19"), False)]))
    run(w, site, T0 + timedelta(minutes=6))
    msg = texts(rec)[-1]
    assert msg.startswith(DIGEST) and "clash: P3 English" in msg


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


def test_backlog_pairs_back_to_back_open_classes():
    site = ScheduleSite()
    site.makeup = [[
        ("HMU-A", "P3 English", "Centre S", "17 Dec 2026 10:00 AM–17 Dec 2026 12:00 PM"),
        ("HMU-B", "P5 Science", "Centre S", "17 Dec 2026 12:45 PM–17 Dec 2026 02:45 PM"),
        ("HMU-C", "P2 Maths", "Centre T", "17 Dec 2026 10:00 AM–17 Dec 2026 12:00 PM"),
    ]]
    w, rec, _ = make(site, FakeCalendar())
    run(w, site, T0)
    msgs = texts(rec)
    best = next(t for t in msgs if t.startswith("📋 <b>HOLIDAY MAKE-UP · ⭐ BEST BETS"))
    others = next(t for t in msgs if t.startswith("📋 <b>HOLIDAY MAKE-UP · OTHERS"))
    assert ("📅 <b>DECEMBER 2026</b>\n\n<b>Thu 17 Dec</b>\n📍 Centre S · 🔗 10am → 12:45pm\n"
            "• 10am–12pm: P3 English\n• 12:45–2:45pm: P5 Science") in best
    assert "Centre T" not in best
    assert "<b>Thu 17 Dec</b>\n📍 Centre T\n• 10am–12pm: P2 Maths" in others
    assert "🔗" not in others.split("⚠️ check</i>", 1)[1]


def test_backlog_names_partner_in_the_other_list():
    site = ScheduleSite()
    site.relief = {1: [("RS-1", "P3 Maths", "Centre S", [("MS Week 8", "17 Dec 2026 03:00 PM–17 Dec 2026 05:00 PM", False)])]}
    site.makeup = [[("HMU-B", "P5 Science", "Centre S", "17 Dec 2026 12:45 PM–17 Dec 2026 02:45 PM")]]
    w, rec, _ = make(site, FakeCalendar())
    run(w, site, T0)
    best = next(t for t in texts(rec) if t.startswith("📋 <b>RELIEF · ⭐ BEST BETS"))
    assert "📍 Centre S · 🔗 next to 12:45pm P5 Science (make-up)\n• 3–5pm: P3 Maths" in best


def test_backlog_skips_days_blocked_by_an_all_day_trip():
    site = ScheduleSite()
    trip = Event("Trip", datetime(2026, 12, 13), datetime(2026, 12, 20), all_day=True, free=True)
    w, rec, _ = make(site, FakeCalendar([trip]))
    run(w, site, T0)
    msgs = texts(rec)
    assert not any(t.startswith("📋 <b>HOLIDAY MAKE-UP") for t in msgs)  # both make-ups are 14/15 Dec
    relief = "\n".join(t for t in msgs if t.startswith("📋 <b>RELIEF"))
    assert "Sun 6 Dec" in relief and "Sat 12 Dec" in relief
