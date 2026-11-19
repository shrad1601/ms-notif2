"""Builds HTML that mirrors the real Dash bulletin markup (captured 19 Nov 2026) with made-up classes."""
import uuid


def _uuid(seed: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, seed))


def relief_card(code, name, centre, lessons):
    """lessons: list of (week, "27 Nov 2026 05:15 PM–27 Nov 2026 07:15 PM", pending: bool)"""
    rows = []
    open_ids = []
    for week, rng, pending in lessons:
        if pending:
            action = f'<span class="pill-bulletin-bid-placed">Pending</span>' \
                     f'<form method="post" action="/teacher/bulletin/{_uuid(code + rng + "w")}/withdraw/">' \
                     f'<button type="submit" class="btn-bulletin-withdraw">Withdraw</button></form>'
        else:
            rid = _uuid(code + rng)
            open_ids.append(rid)
            action = f'<form method="post" action="/teacher/bulletin/{rid}/bid/"><input type="hidden" name="csrfmiddlewaretoken" value="TESTTOKEN">' \
                     f'<input type="hidden" name="category" value="relief-cover">' \
                     f'<button type="submit" class="btn-bulletin-bid">I\'m Interested</button></form>'
        rows.append(
            f'<div class="card-bulletin-session"><div class="card-bulletin-session-info">'
            f'<span class="card-bulletin-date-line"><span class="card-bulletin-ms-week">{week}</span>'
            f'<span>{rng}</span></span></div><div class="card-bulletin-actions">{action}</div></div>')
    bid_all = ""
    if len(open_ids) > 1:
        inputs = "".join(f'<input type="hidden" name="assignment_request_ids" value="{i}">' for i in open_ids)
        bid_all = f'<form method="post" action="/teacher/bulletin/bid-all/" class="card-bulletin-header-action"><input type="hidden" name="csrfmiddlewaretoken" value="TESTTOKEN">' \
                  f'{inputs}<button type="submit" class="btn-bulletin-bid-all">Interested in all lessons</button></form>'
    return (f'<article class="card-bulletin"><div class="card-bulletin-header"><div class="card-bulletin-title-group">'
            f'<div class="pills-bulletin"><span class="pill-bulletin-assignment">Relief cover</span></div>'
            f'<span class="card-bulletin-class-code">{name}</span>'
            f'<span class="card-bulletin-schedule">{centre}</span>'
            f'<span class="card-bulletin-reference">Class code: {code}</span></div>{bid_all}</div>'
            f'{"".join(rows)}</article>')


def relief_page(total, page, pages, cards):
    nav = f'<nav class="bulletin-pagination"><span>Page {page} of {pages}</span>' \
          f'<span class="bulletin-pagination-links"><a href="/teacher/bulletin/?relief_page={page + 1}">Next →</a></span></nav>'
    return (f'<html><head><title>Bulletin</title></head><body><main><div id="tab-available" class="tab-content active">'
            f'<section class="bulletin-section"><h3 id="relief-available-heading" class="bulletin-section-heading">'
            f'Relief Cover ({total})</h3>{"".join(cards)}</section></div>'
            f'{nav}<div id="tab-my-bids" class="tab-content"><h3 id="relief-bids-heading">Relief Cover (0)</h3></div>'
            f'</main></body></html>')


def makeup_card(code, name, centre, rng, closes="13 Dec 2026 11:59 PM"):
    return (f'<article class="card-bulletin"><div class="card-bulletin-header"><div class="card-bulletin-title-group">'
            f'<div class="card-bulletin-permanent-summary">'
            f'<span class="card-bulletin-permanent-class-name">{name}</span>'
            f'<span class="card-bulletin-permanent-schedule"> {centre} · {rng} </span>'
            f'<span class="card-bulletin-permanent-class-code">Class code: {code}</span></div>'
            f'<span class="card-bulletin-commitment">Interest covers this 1 lesson.</span></div>'
            f'<form method="post" action="/teacher/bulletin/permanent/{_uuid(code)}/bid/" class="card-bulletin-actions"><input type="hidden" name="csrfmiddlewaretoken" value="TESTTOKEN">'
            f'<button type="submit" class="btn-bulletin-bid">I\'m Interested</button></form></div>'
            f'<div class="card-bulletin-details"><div class="card-bulletin-detail-row">'
            f'<span class="card-bulletin-detail-label">Bidding closes</span><span>{closes}</span></div></div></article>')


def makeup_page(cards, next_href=None):
    link = f'<span class="bulletin-pagination-links"><a href="{next_href}">More classes →</a></span>' if next_href else ""
    return (f'<html><head><title>Bulletin</title></head><body><main><div id="tab-available" class="tab-content active">'
            f'<section class="bulletin-section"><h3 id="permanent-available-heading" class="bulletin-section-heading">'
            f'Holiday Make-Up</h3>{"".join(cards)}<nav class="bulletin-pagination">'
            f'<span>Showing a bounded set of eligible classes</span>{link}</nav></section></div>'
            f'<div id="tab-my-bids" class="tab-content"><h3 id="permanent-bids-heading">Holiday Make-Up (0)</h3></div>'
            f'</main></body></html>')


def schedule_week(monday, entries):
    """entries: list of (day_index 0-6, "5:15 PM - 7:15 PM", class_name, centre, relief: bool)"""
    from datetime import timedelta
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    cols = []
    for i in range(7):
        d = monday + timedelta(days=i)
        cards = "".join(
            f'<div class="card-zoom-my-schedule-session">'
            f'{"<span class=badge-zoom-my-schedule-relief>Relief</span>" if relief else ""}'
            f'<div class="card-zoom-my-schedule-time"> {t} </div>'
            f'<div class="card-zoom-my-schedule-class-name">{name}</div>'
            f'<div class="card-zoom-my-schedule-centre">{centre}</div></div>'
            for (di, t, name, centre, relief) in entries if di == i) or "<p>No classes scheduled.</p>"
        cols.append(f'<div class="column-zoom-my-schedule-overview-day"><div class="head">{days[i]} {d.day} '
                    f'{d:%b}</div><div class="list-zoom-my-schedule-overview-entries">{cards}</div></div>')
    return (f'<html><head><title>My Class Schedule</title></head><body><main>'
            f'<div class="schedule-legend"><span class="badge-zoom-my-schedule-relief schedule-legend-swatch">Relief</span></div>'
            f'{"".join(cols)}</main></body></html>')


LOGIN_PAGE ='<!DOCTYPE html><html><head><meta charset="utf-8"><title>Sign in · Enrollment System</title></head><body>login</body></html>'


def request_id(code, rng):
    """The id the fake relief page gives a lesson."""
    return _uuid(code + rng)


def makeup_request_id(code):
    return _uuid(code)


def relief_withdraw_id(code, rng):
    return _uuid(code + rng + "w")


def makeup_withdraw_id(code):
    return _uuid(code + "w")


def makeup_my_bids(bids):
    """bids: {class code: status}, e.g. {"HMU-1": "Pending", "HMU-2": "Withdrawn"}"""
    cards = []
    for code, status in bids.items():
        form = (f'<form method="post" action="/teacher/bulletin/permanent/{makeup_withdraw_id(code)}/withdraw/">'
                f'<input type="hidden" name="csrfmiddlewaretoken" value="TESTTOKEN">'
                f'<button type="submit" class="btn-bulletin-withdraw">Withdraw</button></form>') if status == "Pending" else ""
        cards.append(f'<article class="card-bulletin"><div class="pills-bulletin">'
                     f'<span class="pill-bulletin-assignment">Class assignment</span></div>'
                     f'<span class="card-bulletin-permanent-class-code">Class code: {code}</span>'
                     f'<span class="pill-bulletin-bid-placed">{status}</span>{form}</article>')
    return (f'<html><head><title>Bulletin</title></head><body><main><div id="tab-my-bids">'
            f'{"".join(cards)}</div></main></body></html>')
