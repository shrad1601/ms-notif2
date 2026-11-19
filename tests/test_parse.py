import pytest

from fakepages import LOGIN_PAGE, makeup_card, makeup_page, relief_card, relief_page
from watcher.parse import (
    LayoutChanged,
    SessionExpired,
    check_logged_in,
    parse_makeup_page,
    parse_range,
    parse_relief_page,
)


def test_parse_range_en_dash():
    assert parse_range("27 Nov 2026 05:15 PM–27 Nov 2026 07:15 PM") == ("2026-11-27T17:15", "2026-11-27T19:15")


def test_relief_page():
    html = relief_page(268, 1, 14, [
        relief_card("XX2027-P6E PW-Fri-1", "P6 Test Writers", "Somewhere Plaza", [
            ("MS Week 4", "27 Nov 2026 05:15 PM–27 Nov 2026 07:15 PM", False),
            ("MS Week 5", "04 Dec 2026 05:15 PM–04 Dec 2026 07:15 PM", True),
            ("MS Week 6", "11 Dec 2026 05:15 PM–11 Dec 2026 07:15 PM", False),
        ]),
        relief_card("YY2027-P3M-Sat-1", "P3 Maths", "Elsewhere", [
            ("MS Week 4", "28 Nov 2026 09:00 AM–28 Nov 2026 11:00 AM", False),
        ]),
    ])
    total, page, pages, lessons = parse_relief_page(html)
    assert (total, page, pages) == (268, 1, 14)
    assert len(lessons) == 4
    first = lessons[0]
    assert first.class_code == "XX2027-P6E PW-Fri-1"
    assert first.class_name == "P6 Test Writers"
    assert first.centre == "Somewhere Plaza"
    assert first.week == "MS Week 4"
    assert first.start == "2026-11-27T17:15" and first.end == "2026-11-27T19:15"
    assert first.status == "open" and first.request_id
    assert lessons[1].status == "pending" and lessons[1].request_id is None
    assert lessons[0].key == "relief|XX2027-P6E PW-Fri-1|2026-11-27T17:15"


def test_relief_single_page_without_nav():
    html = relief_page(1, 1, 1, [relief_card("A-1", "A", "C", [("MS Week 1", "01 Dec 2026 09:00 AM–01 Dec 2026 11:00 AM", False)])])
    html = html.replace('<nav class="bulletin-pagination">', '<div>').replace("</nav>", "</div>")
    total, page, pages, lessons = parse_relief_page(html)
    assert (total, page, pages, len(lessons)) == (1, 1, 1, 1)


def test_makeup_page():
    html = makeup_page([
        makeup_card("HMU-XX2027-P3M-Mon-9", "P3 Test Maths", "Somewhere", "14 Dec 2026 10:00 AM–14 Dec 2026 12:00 PM"),
    ], next_href="/teacher/bulletin/?after_id=abc")
    lessons, nxt = parse_makeup_page(html)
    assert nxt == "/teacher/bulletin/?after_id=abc"
    assert len(lessons) == 1
    les = lessons[0]
    assert (les.kind, les.class_code, les.centre) == ("makeup", "HMU-XX2027-P3M-Mon-9", "Somewhere")
    assert les.start == "2026-12-14T10:00" and les.closes == "2026-12-13T23:59"
    assert les.request_id


def test_makeup_last_page_has_no_next():
    lessons, nxt = parse_makeup_page(makeup_page([]))
    assert lessons == [] and nxt is None


def test_login_page_detected():
    with pytest.raises(SessionExpired):
        check_logged_in(LOGIN_PAGE)
    with pytest.raises(SessionExpired):
        check_logged_in("<html></html>", "https://dash.mindstretcher.com/auth/login/?next=/")


def test_layout_change_detected():
    with pytest.raises(LayoutChanged):
        parse_relief_page("<html><main>something else</main></html>")
    with pytest.raises(LayoutChanged):
        parse_makeup_page("<html><main>something else</main></html>")
