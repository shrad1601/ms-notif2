import copy
from datetime import datetime, timedelta
from urllib.parse import urljoin

from fakepages import LOGIN_PAGE, makeup_card, makeup_page, relief_card, relief_page
from watcher.fetch import BASE, MAKEUP_URL, FixtureClient, relief_page_url
from watcher.main import Watcher
from watcher.notify import MAX_LEN, Recorder, format_new
from watcher.state import FileStore, decrypt, empty_state, encrypt

T0 = datetime(2026, 11, 20, 2, 0)  # 10:00 SGT, not quiet hours
WK = "{d} Dec 2026 02:00 PM–{d} Dec 2026 04:00 PM"


class MemoryStore:
    def __init__(self):
        self.state = None
        self.saves = 0

    def load(self):
        return copy.deepcopy(self.state) if self.state else empty_state()

    def save(self, state):
        self.state = copy.deepcopy(state)
        self.saves += 1


class Site:
    """Two relief pages + two make-up pages that tests can edit."""

    def __init__(self):
        self.relief = {
            1: [("AA-1", "P5 English", "Centre A", [("MS Week 4", WK.format(d="05"), True), ("MS Week 5", WK.format(d="12"), False)])],
            2: [("BB-1", "P4 Science", "Centre B", [("MS Week 4", WK.format(d="06"), False)])],
        }
        self.makeup = [[("HMU-1", "P3 Maths", "Centre C", WK.format(d="14"))], [("HMU-2", "P2 English", "Centre D", WK.format(d="15"))]]
        self.total = None
        self.expired = False
        self.broken_pages = set()

    def client(self):
        pages = {}
        n_pages = len(self.relief)
        total = self.total if self.total is not None else sum(len(v) for v in self.relief.values())
        for n, cards in self.relief.items():
            if n in self.broken_pages:
                continue
            pages[relief_page_url(n)] = relief_page(total, n, n_pages, [relief_card(*c) for c in cards])
        url = MAKEUP_URL
        for i, cards in enumerate(self.makeup):
            nxt = f"/teacher/bulletin/?tab=available&category=holiday-make-up&after_id={i}" if i + 1 < len(self.makeup) else None
            pages[url] = makeup_page([makeup_card(*c) for c in cards], nxt)
            url = urljoin(BASE, nxt) if nxt else None
        if self.expired:
            pages = {u: LOGIN_PAGE for u in pages}
        return FixtureClient(pages)


def make(site, store=None):
    store = store or MemoryStore()
    rec = Recorder()
    w = Watcher(site.client(), store, rec)
    return w, rec, store


def run(w, site, now, **kw):
    w.client = site.client()
    w.check(now=now, **kw)
    return w.client.requests_made


def texts(rec):
    return [t for t, _ in rec.sent]


def test_first_run_is_baseline_only():
    site = Site()
    w, rec, store = make(site)
    run(w, site, T0)
    assert len(rec.sent) == 1 and "now watching" in rec.sent[0][0]
    assert "2 relief classes (3 lessons)" in rec.sent[0][0]
    assert "2 make-up classes (2 lessons)" in rec.sent[0][0]
    assert store.state["baseline_done"] == {"relief": True, "makeup": True}


def test_quick_check_only_when_nothing_changes():
    site = Site()
    w, rec, store = make(site)
    run(w, site, T0)
    saves = store.saves
    reqs = run(w, site, T0 + timedelta(minutes=6))
    assert reqs == 1
    assert len(rec.sent) == 1
    assert store.saves == saves  # nothing changed, nothing pushed


def test_new_class_triggers_full_scan_and_alert():
    site = Site()
    w, rec, _ = make(site)
    run(w, site, T0)
    site.relief[2].append(("CC-1", "P6 Maths", "Centre E", [("MS Week 6", WK.format(d="19"), False), ("MS Week 7", WK.format(d="26"), False)]))
    reqs = run(w, site, T0 + timedelta(minutes=6))
    assert reqs == 2  # page 1 + page 2
    msgs = texts(rec)[1:]
    assert len(msgs) == 1
    assert "🆕" in msgs[0] and "P6 Maths" in msgs[0] and "Sat 19 Dec" in msgs[0] and "Sat 26 Dec" in msgs[0]
    assert "➕" not in msgs[0]


def test_new_week_waits_for_full_scan_and_mentions_existing_bids():
    site = Site()
    w, rec, _ = make(site)
    run(w, site, T0)
    site.relief[1][0][3].append(("MS Week 6", WK.format(d="19"), False))  # count of classes unchanged
    run(w, site, T0 + timedelta(minutes=30))
    assert len(rec.sent) == 1  # quick check can't see it yet
    run(w, site, T0 + timedelta(minutes=91))
    msgs = texts(rec)[1:]
    assert len(msgs) == 1
    assert "➕" in msgs[0] and "1 new week on P5 English" in msgs[0]
    assert "already bid on: Wk 4" in msgs[0]


def test_lesson_missing_for_one_scan_is_not_realerted():
    site = Site()
    w, rec, _ = make(site)
    run(w, site, T0)
    gone = site.relief.pop(2)
    run(w, site, T0 + timedelta(minutes=6))
    site.relief[2] = gone
    run(w, site, T0 + timedelta(minutes=12))
    assert len(rec.sent) == 1


def test_relisted_after_a_day_is_alerted_again():
    site = Site()
    w, rec, _ = make(site)
    run(w, site, T0)
    gone = site.relief.pop(2)
    run(w, site, T0 + timedelta(minutes=6))
    site.relief[2] = gone
    run(w, site, T0 + timedelta(hours=30))
    msgs = texts(rec)[1:]
    assert len(msgs) == 1
    assert "back on bulletin" in msgs[0] and "P4 Science" in msgs[0]


def test_long_outage_does_not_flood_alerts():
    site = Site()
    w, rec, _ = make(site)
    run(w, site, T0)
    run(w, site, T0 + timedelta(hours=40), force_full=True)  # e.g. cookie was expired for a day and a half
    assert len(rec.sent) == 1


def test_new_makeup_class_alerted_on_hourly_scan():
    site = Site()
    w, rec, _ = make(site)
    run(w, site, T0)
    site.makeup[1].append(("HMU-3", "P1 Maths", "Centre F", WK.format(d="16")))
    run(w, site, T0 + timedelta(minutes=30))
    assert len(rec.sent) == 1
    run(w, site, T0 + timedelta(minutes=61))
    msgs = texts(rec)[1:]
    assert len(msgs) == 1 and "Make-up" in msgs[0] and "P1 Maths" in msgs[0] and "Bidding closes" in msgs[0]


def test_incomplete_baseline_waits_for_a_complete_scan():
    site = Site()
    site.broken_pages = {2}
    w, rec, store = make(site)
    run(w, site, T0)
    assert store.state["baseline_done"]["relief"] is False
    assert all("relief" not in t for t in texts(rec))
    site.broken_pages = set()
    run(w, site, T0 + timedelta(minutes=6))
    assert store.state["baseline_done"]["relief"] is True
    assert sum("🆕" in t for t in texts(rec)) == 0


def test_expired_session_alerts_once_and_recovers():
    site = Site()
    w, rec, _ = make(site)
    run(w, site, T0)
    site.expired = True
    run(w, site, T0 + timedelta(minutes=6))
    assert "cookie" in texts(rec)[-1]
    n = len(rec.sent)
    assert run(w, site, T0 + timedelta(minutes=12)) == 0  # waits 30 min before retrying
    run(w, site, T0 + timedelta(minutes=40))  # retried, still expired, but within 3h cooldown
    assert len(rec.sent) == n
    site.expired = False
    run(w, site, T0 + timedelta(minutes=80))
    assert "working again" in texts(rec)[-1]


def test_layout_change_alerts():
    site = Site()
    w, rec, _ = make(site)
    w.client = FixtureClient({relief_page_url(1): "<html><main>new design</main></html>"})
    w.check(now=T0)
    assert "looks different" in texts(rec)[-1]


def test_quiet_hours_are_silent():
    site = Site()
    w, rec, _ = make(site)
    run(w, site, datetime(2026, 11, 20, 16, 30))  # 00:30 SGT
    assert rec.sent[-1][1] is True
    w2, rec2, _ = make(Site())
    run(w2, Site(), T0)
    assert rec2.sent[-1][1] is False


def test_long_alerts_are_split():
    site = Site()
    w, rec, _ = make(site)
    run(w, site, T0)
    site.relief[3] = [(f"Z-{i}", f"Class {i} " + "x" * 80, "Centre Z", [("MS Week 9", WK.format(d="20"), False)]) for i in range(80)]
    run(w, site, T0 + timedelta(minutes=6))
    msgs = texts(rec)[1:]
    assert len(msgs) > 1
    assert all(len(m) <= 4096 for m in msgs)
    assert sum(m.count("🆕") for m in msgs) == 80


def test_html_is_escaped():
    from watcher.core import NewItem
    from watcher.parse import Lesson
    les = Lesson("relief", "A<1>", "P5 <b>&", "C", "2026-12-01T09:00", "2026-12-01T11:00", "MS Week 5")
    out = format_new([NewItem(les, True)], empty_state())[0]
    assert "P5 &lt;b&gt;&amp;" in out and "A&lt;1&gt;" in out


def test_state_encryption_roundtrip(tmp_path):
    from cryptography.fernet import Fernet
    key = Fernet.generate_key().decode()
    state = empty_state()
    state["relief_total"] = 5
    assert decrypt(encrypt(state, key), key) == state
    fs = FileStore(str(tmp_path / "s.json"))
    fs.save(state)
    assert fs.load() == state


def test_max_len_constant_leaves_room():
    assert MAX_LEN < 4096 - 200
