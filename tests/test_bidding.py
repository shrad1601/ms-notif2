from datetime import datetime, timedelta
from itertools import count

from fakepages import makeup_my_bids, makeup_request_id, request_id
from test_watcher import T0, WK, run, texts
from test_watcher_fit import MONDAY, FakeCalendar, ScheduleSite, ev, make
from watcher.bid import MAKEUP_MY_BIDS_URL
from watcher.fetch import BASE

_ids = count(1000)


class BidSite(ScheduleSite):
    """A fake Dash that accepts bids: relief lessons turn Pending, make-ups move to My Bids."""

    def __init__(self):
        super().__init__()
        self.makeup_bids: list[str] = []
        self.client_obj = None

    def client(self):
        c = super().client()
        c.pages[MAKEUP_MY_BIDS_URL] = makeup_my_bids(self.makeup_bids)
        c.on_post = self._on_post
        self.client_obj = c
        return c

    def _mark_relief(self, rids):
        for page, cards in self.relief.items():
            for code, name, centre, lessons in cards:
                for i, (week, rng, pending) in enumerate(lessons):
                    if request_id(code, rng) in rids:
                        lessons[i] = (week, rng, True)

    def _on_post(self, url, data):
        fields = dict(data)
        assert fields.get("csrfmiddlewaretoken") == "TESTTOKEN"
        path = url.removeprefix(BASE)
        if path == "/teacher/bulletin/bid-all/":
            self._mark_relief({v for k, v in data if k == "assignment_request_ids"})
        elif path.startswith("/teacher/bulletin/permanent/"):
            rid = path.split("/")[4]
            for page in self.makeup:
                for card in list(page):
                    if makeup_request_id(card[0]) == rid:
                        page.remove(card)
                        self.makeup_bids.append(card[0])
        else:
            self._mark_relief({path.split("/")[3]})
        live = self.client_obj
        fresh = self.client()  # rebuild pages from the updated data
        self.client_obj = live
        live.pages.clear()
        live.pages.update(fresh.pages)
        return 200, "<html><main>Interest registered</main></html>"


def buttons(rec):
    out = []
    for idx, (_, _, keyboard, _) in enumerate(rec.sent):
        for row in keyboard or []:
            for b in row:
                out.append((b["text"], b["callback_data"], idx + 1))
    return out


def tap(w, rec, site, label, who="me"):
    text, data, mid = next(b for b in reversed(buttons(rec)) if b[0].startswith(label))
    rec.queue.append({"update_id": next(_ids), "callback_query": {
        "id": f"cb{mid}", "from": {"id": who}, "message": {"message_id": mid, "chat": {"id": who}}, "data": data}})
    w.client = site.client()
    w.poll(5)


def posts(site):
    return [(url.removeprefix(BASE), data) for url, data, _ in site.client_obj.posts]


def setup_starred_class(site, both_weeks_fit):
    # my Dash class right before the new class on both Saturdays -> back-to-back -> ⭐
    for wk in (4, 5):
        site.schedule[MONDAY + timedelta(weeks=wk)] = [(5, "11:45 AM - 1:45 PM", "P3 English", "Centre E", False)]
    clash = [] if both_weeks_fit else [ev("Other job", "2026-12-19T14:30", "2026-12-19T16:30", "Some Mall")]
    w, rec, store = make(site, FakeCalendar(clash))
    run(w, site, T0)
    site.relief[2].append(("CC-1", "P6 Maths", "Centre E",
                           [("MS Week 7", WK.format(d="19"), False), ("MS Week 8", WK.format(d="26"), False)]))
    run(w, site, T0 + timedelta(minutes=6))
    return w, rec, store


def test_tap_bids_only_the_fitting_week_and_confirms():
    site = BidSite()
    w, rec, store = setup_starred_class(site, both_weeks_fit=False)
    assert [b[0] for b in buttons(rec)][-2:] == ["✅ Bid", "Skip"]
    tap(w, rec, site, "✅ Bid")
    assert [p[0] for p in posts(site)] == [f"/teacher/bulletin/{request_id('CC-1', WK.format(d='26'))}/bid/"]
    assert ("tab", "my-bids") in posts(site)[0][1] and ("category", "relief-cover") in posts(site)[0][1]
    reply = texts(rec)[-1]
    assert reply.startswith("✅ <b>Bid placed</b>: P6 Maths · Centre E")
    assert "Sat 26 Dec, 2pm–4pm" in reply and "Pending" in reply
    assert rec.sent[-1][3] is not None  # sent as a reply to the alert
    assert rec.edits[-1][1][-1] == [{"text": "✅ Bid placed · P6 Maths · Centre E", "callback_data": "noop:"}]
    assert rec.answers[0][1].startswith("On it")


def test_all_weeks_use_interested_in_all_lessons():
    site = BidSite()
    w, rec, _ = setup_starred_class(site, both_weeks_fit=True)
    tap(w, rec, site, "✅ Bid all 2 wks")
    (url, data), = posts(site)
    assert url == "/teacher/bulletin/bid-all/"
    ids = {v for k, v in data if k == "assignment_request_ids"}
    assert ids == {request_id("CC-1", WK.format(d="19")), request_id("CC-1", WK.format(d="26"))}
    assert "(Interested in all lessons)" in texts(rec)[-1]


def test_double_tap_bids_once():
    site = BidSite()
    w, rec, _ = setup_starred_class(site, both_weeks_fit=True)
    data = next(b[1] for b in buttons(rec) if b[0].startswith("✅ Bid all"))
    tap(w, rec, site, "✅ Bid all")
    n_posts, n_sent = len(posts(site)), len(rec.sent)
    rec.queue.append({"update_id": next(_ids), "callback_query": {
        "id": "again", "from": {"id": "me"}, "message": {"message_id": 1, "chat": {"id": "me"}}, "data": data}})
    w.poll(5)
    assert len(posts(site)) == n_posts and len(rec.sent) == n_sent
    assert rec.answers[-1][1].startswith("Already handled")


def test_tap_from_someone_else_is_ignored():
    site = BidSite()
    w, rec, _ = setup_starred_class(site, both_weeks_fit=True)
    tap(w, rec, site, "✅ Bid all", who="stranger")
    assert posts(site) == []
    assert rec.answers[-1][1] == "Not allowed."


def test_class_gone_before_tap():
    site = BidSite()
    w, rec, _ = setup_starred_class(site, both_weeks_fit=True)
    site.relief[2] = [c for c in site.relief[2] if c[0] != "CC-1"]
    tap(w, rec, site, "✅ Bid all")
    assert posts(site) == []
    reply = texts(rec)[-1]
    assert reply.startswith("❌ <b>Nothing bid</b>") and "2 weeks no longer on the bulletin" in reply
    assert rec.edits[-1][1][-1][0]["text"].startswith("❌ Not bid")


def test_calendar_rechecked_before_bidding():
    site = BidSite()
    w, rec, _ = setup_starred_class(site, both_weeks_fit=True)
    w.calendar.items.append(ev("Dentist", "2026-12-19T13:00", "2026-12-19T15:00", "Clinic"))
    tap(w, rec, site, "✅ Bid all")
    (url, _), = posts(site)
    assert url == f"/teacher/bulletin/{request_id('CC-1', WK.format(d='26'))}/bid/"  # not bid-all any more
    reply = texts(rec)[-1]
    assert "Sat 26 Dec" in reply and "skipped 19 Dec: clash: Dentist" in reply


def test_skip_does_nothing():
    site = BidSite()
    w, rec, _ = setup_starred_class(site, both_weeks_fit=True)
    tap(w, rec, site, "Skip")
    assert posts(site) == []
    assert rec.edits[-1][1][-1] == [{"text": "Skipped", "callback_data": "noop:"}]


def test_bid_from_the_best_bets_list_for_a_makeup():
    site = BidSite()
    site.makeup = [[
        ("HMU-A", "P3 English", "Centre S", "17 Dec 2026 10:00 AM–17 Dec 2026 12:00 PM"),
        ("HMU-B", "P5 Science", "Centre S", "17 Dec 2026 12:45 PM–17 Dec 2026 02:45 PM"),
    ]]
    w, rec, _ = make(site, FakeCalendar())
    run(w, site, T0)
    labels = [b[0] for b in buttons(rec)]
    assert "Bid · 17 Dec 10am · P3 English · Centre S" in labels
    assert "Bid · 17 Dec 12:45pm · P5 Science · Centre S" in labels
    tap(w, rec, site, "Bid · 17 Dec 12:45pm")
    (url, data), = posts(site)
    assert url == f"/teacher/bulletin/permanent/{makeup_request_id('HMU-B')}/bid/"
    assert ("category", "holiday-make-up") in data
    assert texts(rec)[-1].startswith("✅ <b>Bid placed</b>: P5 Science · Centre S")


def test_csrf_cookie_fallback_is_read_from_the_cookie_string():
    from watcher.fetch import Client
    assert Client("a=1; csrftoken=XYZ123; sessionid=s").csrf_cookie == "XYZ123"
    assert Client("a=1; sessionid=s").csrf_cookie is None


def test_expired_session_during_bid():
    site = BidSite()
    w, rec, _ = setup_starred_class(site, both_weeks_fit=True)
    site.expired = True
    tap(w, rec, site, "✅ Bid all")
    assert "session has expired" in texts(rec)[-1]
