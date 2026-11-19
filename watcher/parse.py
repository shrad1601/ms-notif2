import re
from dataclasses import dataclass, field
from datetime import datetime

from bs4 import BeautifulSoup

DATE_FMT = "%d %b %Y %I:%M %p"
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
CLASS_CODE_PREFIX = re.compile(r"^\s*Class code:\s*", re.I)


class SessionExpired(Exception):
    pass


class LayoutChanged(Exception):
    pass


@dataclass
class Lesson:
    kind: str  # "relief" or "makeup"
    class_code: str
    class_name: str
    centre: str
    start: str  # ISO local time (account timezone, SGT)
    end: str
    week: str | None = None
    request_id: str | None = None
    status: str = "open"  # "open" or the pill text lowercased, e.g. "pending"
    closes: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.kind}|{self.class_code}|{self.start}"

    @property
    def class_key(self) -> str:
        return f"{self.kind}|{self.class_code}"


def _text(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip() if el else ""


def parse_range(text: str) -> tuple[str, str]:
    parts = re.split(r"\s*[–—]\s*", text.strip(), maxsplit=1)
    if len(parts) != 2:
        raise LayoutChanged(f"unexpected date range format: {text!r}")
    start = datetime.strptime(parts[0].strip(), DATE_FMT)
    end = datetime.strptime(parts[1].strip(), DATE_FMT)
    return start.isoformat(timespec="minutes"), end.isoformat(timespec="minutes")


def parse_dt(text: str) -> str | None:
    try:
        return datetime.strptime(text.strip(), DATE_FMT).isoformat(timespec="minutes")
    except ValueError:
        return None


def check_logged_in(html: str, final_url: str = "") -> None:
    if "/auth/login" in final_url:
        raise SessionExpired("redirected to login")
    head = html[:4000]
    if re.search(r"<title>\s*Sign in", head, re.I):
        raise SessionExpired("got the sign-in page")


def _available_tab(soup):
    tab = soup.select_one("#tab-available")
    if tab is None:
        raise LayoutChanged("no #tab-available section")
    return tab


def parse_relief_page(html: str) -> tuple[int, int, int, list[Lesson]]:
    """Returns (total classes, this page, page count, lessons on this page)."""
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.select_one("#relief-available-heading")
    if heading is None:
        raise LayoutChanged("no relief heading")
    m = re.search(r"\((\d+)\)", _text(heading))
    if not m:
        raise LayoutChanged(f"no count in relief heading: {_text(heading)!r}")
    total = int(m.group(1))

    page, pages = 1, 1
    nav = soup.select_one("nav.bulletin-pagination")
    if nav is not None:
        pm = re.search(r"Page\s+(\d+)\s+of\s+(\d+)", _text(nav))
        if pm:
            page, pages = int(pm.group(1)), int(pm.group(2))

    lessons: list[Lesson] = []
    for card in _available_tab(soup).select("article.card-bulletin"):
        name = _text(card.select_one(".card-bulletin-class-code"))
        centre = _text(card.select_one(".card-bulletin-schedule"))
        code = CLASS_CODE_PREFIX.sub("", _text(card.select_one(".card-bulletin-reference")))
        if not code:
            raise LayoutChanged("relief card without class code")
        for row in card.select(".card-bulletin-session"):
            date_line = row.select_one(".card-bulletin-date-line")
            spans = [s for s in date_line.find_all("span", recursive=False)] if date_line else []
            range_span = next((s for s in reversed(spans) if "card-bulletin-ms-week" not in (s.get("class") or [])), None)
            if range_span is None:
                raise LayoutChanged("relief lesson without date range")
            start, end = parse_range(_text(range_span))
            week = _text(row.select_one(".card-bulletin-ms-week")) or None
            form = row.select_one('form[action$="/bid/"]')
            rid = None
            if form is not None:
                um = UUID_RE.search(form.get("action", ""))
                rid = um.group(0) if um else None
            pill = row.select_one(".pill-bulletin-bid-placed")
            status = _text(pill).lower() if pill else "open"
            lessons.append(Lesson("relief", code, name, centre, start, end, week, rid, status))
    return total, page, pages, lessons


def parse_makeup_page(html: str) -> tuple[list[Lesson], str | None]:
    """Returns (lessons on this page, href of the "More classes" link or None)."""
    soup = BeautifulSoup(html, "html.parser")
    if soup.select_one("#permanent-available-heading") is None:
        raise LayoutChanged("no make-up heading")

    lessons: list[Lesson] = []
    for card in _available_tab(soup).select("article.card-bulletin"):
        name = _text(card.select_one(".card-bulletin-permanent-class-name"))
        code = CLASS_CODE_PREFIX.sub("", _text(card.select_one(".card-bulletin-permanent-class-code")))
        if not code:
            raise LayoutChanged("make-up card without class code")
        form = card.select_one('form[action*="/bid/"]')
        rid = None
        if form is not None:
            um = UUID_RE.search(form.get("action", ""))
            rid = um.group(0) if um else None
        pill = card.select_one(".pill-bulletin-bid-placed")
        status = _text(pill).lower() if pill else "open"
        closes = None
        for row in card.select(".card-bulletin-detail-row"):
            if _text(row.select_one(".card-bulletin-detail-label")).lower().startswith("bidding closes"):
                spans = row.find_all("span")
                closes = parse_dt(_text(spans[-1])) if spans else None
        schedules = card.select(".card-bulletin-permanent-schedule")
        if not schedules:
            raise LayoutChanged("make-up card without schedule")
        for sched in schedules:
            text = _text(sched)
            if "·" not in text:
                raise LayoutChanged(f"unexpected make-up schedule format: {text!r}")
            centre, rng = (p.strip() for p in text.split("·", 1))
            start, end = parse_range(rng)
            lessons.append(Lesson("makeup", code, name, centre, start, end, None, rid, status, closes))

    next_href = None
    for a in soup.select("nav.bulletin-pagination a[href]"):
        if "more classes" in _text(a).lower():
            next_href = a["href"]
            break
    return lessons, next_href
