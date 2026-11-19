from datetime import date, datetime

import pytest

from fakepages import schedule_week
from watcher.dash_schedule import parse_week, signature
from watcher.fit import Config, Event, Fit, evaluate, rank
from watcher.gcal import to_event
from watcher.parse import LayoutChanged, Lesson

CFG = Config(too_far=["Far Centre", "Remote West"],
             latest_end={"Tue": "17:00", "Thu": "17:00", "Fri": "19:15"},
             soft_keywords=["side gig"])


def L(start, end, centre="Toa Payoh", name="P5 English", code="TP-1", status="open"):
    return Lesson("relief", code, name, centre, start, end, "MS Week 5", None, status)


def ev(title, start, end, location="", **kw):
    return Event(title, datetime.fromisoformat(start), datetime.fromisoformat(end), location, **kw)


SAT = ("2026-12-05T14:00", "2026-12-05T16:00")  # Saturday


def test_fits_when_calendar_empty():
    f = evaluate(L(*SAT), [], CFG)
    assert f.ok and not f.flags and not f.back_to_back


def test_too_far_centre():
    f = evaluate(L(*SAT, centre="Far Centre"), [], CFG)
    assert not f.ok and "too far" in f.reason
    assert not evaluate(L(*SAT, centre="Remote West (Annex)"), [], CFG).ok


def test_latest_end_per_weekday():
    tue_late = L("2026-12-01T17:15", "2026-12-01T19:15")
    tue_ok = L("2026-12-01T15:00", "2026-12-01T17:00")
    assert not evaluate(tue_late, [], CFG).ok and "Tue" in evaluate(tue_late, [], CFG).reason
    assert evaluate(tue_ok, [], CFG).ok
    assert evaluate(L("2026-12-04T17:15", "2026-12-04T19:15"), [], CFG).ok  # Fri until 7:15pm is fine


def test_overlap_is_a_clash():
    f = evaluate(L(*SAT), [ev("Other job", "2026-12-05T15:00", "2026-12-05T17:00", "Some Mall")], CFG)
    assert not f.ok and f.reason == "clash: Other job (3–5pm)"


def test_soft_keyword_overlap_is_flagged_not_blocking():
    f = evaluate(L(*SAT), [ev("Side Gig class", "2026-12-05T13:00", "2026-12-05T15:00", "Somewhere")], CFG)
    assert f.ok and any("clashes with Side Gig class" in x for x in f.flags)


def test_free_events_ignored():
    f = evaluate(L(*SAT), [ev("Maybe lunch", "2026-12-05T14:00", "2026-12-05T15:00", free=True)], CFG)
    assert f.ok and not f.flags


def test_all_day_busy_blocks_the_day():
    f = evaluate(L(*SAT), [ev("Away", "2026-12-05T00:00", "2026-12-06T00:00", all_day=True)], CFG)
    assert not f.ok and "Away" in f.reason and "all day" in f.reason


def test_all_day_trip_marked_free_still_blocks():
    # Google Calendar marks all-day events as "Free" by default
    trip = ev("Trip", "2026-12-04T00:00", "2026-12-20T00:00", all_day=True, free=True)
    assert not evaluate(L(*SAT), [trip], CFG).ok


def test_multi_day_timed_event_marked_free_still_blocks():
    trip = ev("Trip", "2026-12-04T08:00", "2026-12-10T20:00", free=True)
    assert not evaluate(L(*SAT), [trip], CFG).ok


def test_nearby_event_elsewhere_is_flagged_with_gap():
    f = evaluate(L(*SAT), [ev("Other job", "2026-12-05T11:00", "2026-12-05T13:30", "Far Mall")], CFG)
    assert f.ok and f.flags == ["⚠️ 30 min after Other job @ Far Mall"]
    f2 = evaluate(L(*SAT), [ev("Online class", "2026-12-05T16:15", "2026-12-05T18:00", "Online")], CFG)
    assert f2.ok and f2.flags == ["⚠️ 15 min before Online class (online)"]
    f3 = evaluate(L(*SAT), [ev("Party", "2026-12-05T16:00", "2026-12-05T20:00")], CFG)
    assert f3.flags == ["⚠️ right before Party"]


def test_gaps_over_30_min_are_not_flagged():
    f = evaluate(L(*SAT), [ev("Tuition", "2026-12-05T16:45", "2026-12-05T18:00")], CFG)
    assert f.ok and not f.flags


def test_trip_blocks_its_whole_first_day():
    # flight at 7:30pm: a 5:15-7:15pm lesson that day can't happen
    trip = ev("Trip", "2026-12-21T19:30", "2027-01-09T06:00")
    f = evaluate(L("2026-12-21T17:15", "2026-12-21T19:15"), [trip], CFG)
    assert not f.ok and f.reason == "Trip (21 Dec–9 Jan)"
    assert not evaluate(L("2027-01-09T14:00", "2027-01-09T16:00"), [trip], CFG).ok  # day you're back
    assert evaluate(L("2027-01-10T14:00", "2027-01-10T16:00"), [trip], CFG).ok


def test_same_centre_back_to_back():
    f = evaluate(L(*SAT), [ev("P4 Maths", "2026-12-05T11:45", "2026-12-05T13:45", "Toa Payoh")], CFG)
    assert f.ok and f.back_to_back and f.b2b and not f.flags
    assert f.b2b_with == ["after your P4 Maths (11:45am–1:45pm)"]


def test_same_place_ignores_mind_stretcher_and_cc_wording():
    lesson = L(*SAT, centre="Sengkang CC")
    f = evaluate(lesson, [ev("P2 English", "2026-12-05T11:30", "2026-12-05T13:30", "Mind Stretcher Sengkang")], CFG)
    assert f.ok and f.back_to_back and not f.flags


def test_back_to_back_gap_rules():
    wed = ("2026-12-02T12:45", "2026-12-02T14:45")
    before = [ev("Class", "2026-12-02T10:00", "2026-12-02T12:00", "Toa Payoh")]  # 45 min gap
    assert not evaluate(L(*wed), before, CFG).back_to_back  # weekday relief: 30 min max
    makeup = Lesson("makeup", "HMU-1", "P3 English", "Toa Payoh", *wed)
    assert evaluate(makeup, before, CFG).back_to_back  # make-ups: 10am then 12:45pm counts


def test_far_away_in_time_is_ignored():
    f = evaluate(L(*SAT), [ev("Other job", "2026-12-05T10:00", "2026-12-05T12:00", "Far Mall")], CFG)
    assert f.ok and not f.flags


def test_overlapping_pending_bid():
    other = L("2026-12-05T15:00", "2026-12-05T17:00", name="P6 Science", code="OTHER-1", status="pending")
    f = evaluate(L(*SAT), [], CFG, [other])
    assert not f.ok and "pending bid" in f.reason


@pytest.mark.parametrize("fits,taught,continuing,expected", [
    ([Fit(True, back_to_back=True)], True, False, 1),
    ([Fit(True, pairs=["3pm P2 Maths"])], False, True, 1),
    ([Fit(True, back_to_back=True), Fit(False)], False, False, 2),
    ([Fit(True)], False, True, 3),
    ([Fit(True)], True, False, 4),  # covered before but standalone: listed, no ⭐
    ([Fit(True)], False, False, 4),
    ([Fit(False)], True, True, 5),
])
def test_rank(fits, taught, continuing, expected):
    assert rank(fits, taught, continuing) == expected


def test_parse_week_and_signature():
    monday = date(2026, 11, 16)
    html = schedule_week(monday, [
        (0, "5:15 PM - 7:15 PM", "P4 English (High Achiever)", "Online", True),
        (5, "10:00 AM - 12:00 PM", "P3 Maths", "Toa Payoh", False),
    ])
    out = parse_week(html, monday)
    assert len(out) == 2
    assert out[0].start == datetime(2026, 11, 16, 17, 15) and out[0].relief
    assert out[1].start == datetime(2026, 11, 21, 10, 0) and not out[1].relief
    assert signature(out[0].class_name, out[0].centre, out[0].start) == "p4 english (high achiever)|online|Mon|17:15"


def test_parse_week_with_no_classes():
    html = ("<html><body><main><h2>My Class Schedule</h2><p>Selected week · AY2027</p>"
            "<p>No classes scheduled for this week.</p></main></body></html>")
    assert parse_week(html, date(2027, 1, 18)) == []


def test_parse_week_layout_change():
    with pytest.raises(LayoutChanged):
        parse_week("<html><main>new layout</main></html>", date(2026, 11, 16))


def test_gcal_event_conversion():
    timed = to_event({"summary": "Other job", "start": {"dateTime": "2026-12-05T06:00:00Z"},
                      "end": {"dateTime": "2026-12-05T15:00:00+08:00"}, "location": "Far Mall"})
    assert timed.start == datetime(2026, 12, 5, 14, 0) and timed.end == datetime(2026, 12, 5, 15, 0)
    assert not timed.all_day and not timed.free
    allday = to_event({"summary": "Away", "start": {"date": "2026-12-08"}, "end": {"date": "2026-12-13"}})
    assert allday.all_day and allday.start == datetime(2026, 12, 8) and allday.end == datetime(2026, 12, 13)
    assert to_event({"summary": "x", "transparency": "transparent", "start": {"date": "2026-12-01"},
                     "end": {"date": "2026-12-02"}}).free
    assert to_event({"status": "cancelled", "start": {"date": "2026-12-01"}, "end": {"date": "2026-12-02"}}) is None
    assert to_event({"summary": "x", "attendees": [{"self": True, "responseStatus": "declined"}],
                     "start": {"date": "2026-12-01"}, "end": {"date": "2026-12-02"}}) is None
