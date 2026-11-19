import json
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests
from google.auth.transport.requests import Request
from google.oauth2 import service_account

from .fit import Event

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
SGT = timezone(timedelta(hours=8))
API = "https://www.googleapis.com/calendar/v3/calendars/{}/events"


def _to_sgt(value: dict) -> tuple[datetime, bool]:
    if "dateTime" in value:
        dt = datetime.fromisoformat(value["dateTime"].replace("Z", "+00:00"))
        return dt.astimezone(SGT).replace(tzinfo=None), False
    return datetime.fromisoformat(value["date"]), True


def to_event(item: dict) -> Event | None:
    if item.get("status") == "cancelled":
        return None
    for att in item.get("attendees", []):
        if att.get("self") and att.get("responseStatus") == "declined":
            return None
    start, all_day = _to_sgt(item["start"])
    end, _ = _to_sgt(item["end"])
    return Event(
        title=item.get("summary", "(busy)"),
        start=start,
        end=end,
        location=item.get("location", ""),
        all_day=all_day,
        free=item.get("transparency") == "transparent",
    )


class GoogleCalendar:
    def __init__(self, service_account_json: str, calendar_ids: list[str]):
        self.creds = service_account.Credentials.from_service_account_info(
            json.loads(service_account_json), scopes=SCOPES)
        self.calendar_ids = calendar_ids
        self.session = requests.Session()

    def _token(self) -> str:
        if not self.creds.valid:
            self.creds.refresh(Request())
        return self.creds.token

    def events(self, start: datetime, end: datetime) -> list[Event]:
        """All events between two naive SGT datetimes, across the configured calendars."""
        params = {
            "timeMin": start.replace(tzinfo=SGT).isoformat(),
            "timeMax": end.replace(tzinfo=SGT).isoformat(),
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": "2500",
            "fields": "items(summary,start,end,location,transparency,status,attendees(self,responseStatus)),nextPageToken",
        }
        out: list[Event] = []
        for cal in self.calendar_ids:
            page_token = None
            while True:
                if page_token:
                    params["pageToken"] = page_token
                else:
                    params.pop("pageToken", None)
                r = self.session.get(API.format(quote(cal, safe="")), params=params, timeout=20,
                                     headers={"Authorization": f"Bearer {self._token()}"})
                r.raise_for_status()
                data = r.json()
                out += [ev for ev in map(to_event, data.get("items", [])) if ev]
                page_token = data.get("nextPageToken")
                if not page_token:
                    break
        return out
