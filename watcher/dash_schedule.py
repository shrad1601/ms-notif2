"""My Class Schedule (overview view): lessons already assigned to me, incl. relief ones."""
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from bs4 import BeautifulSoup

from .fetch import BASE
from .fit import WEEKDAYS
from .parse import LayoutChanged, _text

TIME_RE = re.compile(r"(\d{1,2}:\d{2}\s*[AP]M)\s*[-–]\s*(\d{1,2}:\d{2}\s*[AP]M)", re.I)


@dataclass
class Assigned:
    start: datetime
    end: datetime
    class_name: str
    centre: str
    relief: bool


def week_url(monday: date) -> str:
    return f"{BASE}/teacher/my-schedule/?schedule_view=overview&week_start={monday:%Y-%m-%d}&month={monday:%Y-%m}"


def parse_week(html: str, monday: date) -> list[Assigned]:
    soup = BeautifulSoup(html, "html.parser")
    columns = soup.select(".column-zoom-my-schedule-overview-day")
    if len(columns) != 7:
        raise LayoutChanged(f"expected 7 day columns in my-schedule overview, got {len(columns)}")
    out = []
    for i, col in enumerate(columns):
        if not _text(col).startswith(WEEKDAYS[i]):
            raise LayoutChanged("my-schedule day columns not in Mon..Sun order")
        day = monday + timedelta(days=i)
        for card in col.select(".card-zoom-my-schedule-session"):
            m = TIME_RE.search(_text(card.select_one(".card-zoom-my-schedule-time")))
            if not m:
                continue
            start, end = (datetime.combine(day, datetime.strptime(t.upper().replace(" ", ""), "%I:%M%p").time())
                          for t in m.groups())
            out.append(Assigned(
                start=start,
                end=end,
                class_name=_text(card.select_one(".card-zoom-my-schedule-class-name")),
                centre=_text(card.select_one(".card-zoom-my-schedule-centre")),
                relief=card.select_one(".badge-zoom-my-schedule-relief") is not None,
            ))
    return out


def signature(class_name: str, centre: str, start: datetime) -> str:
    """Identifies a class by what both the bulletin and my schedule show (no class code on the schedule)."""
    return f"{class_name.strip().lower()}|{centre.strip().lower()}|{WEEKDAYS[start.weekday()]}|{start:%H:%M}"


def fetch_weeks(client, today: date, weeks_back: int = 1, weeks_ahead: int = 9) -> list[Assigned]:
    monday = today - timedelta(days=today.weekday())
    out: list[Assigned] = []
    for w in range(-weeks_back, weeks_ahead + 1):
        week = monday + timedelta(weeks=w)
        out += parse_week(client.get(week_url(week)), week)
    return out
