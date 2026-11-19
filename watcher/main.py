import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

from .core import NewItem, apply_scan, due
from .dash_schedule import fetch_weeks, signature
from .fetch import Client, makeup_full, relief_full, relief_page1
from .fit import Config, Event, evaluate, rank
from .notify import ClassAlert, Recorder, Telegram, format_alerts, format_backlog, group_new, is_quiet
from .parse import LayoutChanged, Lesson, SessionExpired
from .state import FileStore, GitBranchStore

RELIEF_FULL_EVERY = timedelta(minutes=90)
MAKEUP_FULL_EVERY = timedelta(minutes=60)
SCHEDULE_EVERY = timedelta(hours=24)
ALERT_COOLDOWN = timedelta(hours=3)
FAILING_RETRY_EVERY = timedelta(minutes=30)
SGT_OFFSET = timedelta(hours=8)
LESSON_FIELDS = ("kind", "class_code", "class_name", "centre", "start", "end", "week", "request_id", "status", "closes")


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _ts(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _lesson(rec: dict) -> Lesson:
    return Lesson(**{k: rec.get(k) for k in LESSON_FIELDS})


class Watcher:
    def __init__(self, client, store, notifier, cfg: Config | None = None, calendar=None):
        self.client = client
        self.store = store
        self.notifier = notifier
        self.cfg = cfg or Config()
        self.calendar = calendar
        self.state = store.load()
        self.state.setdefault("dash", {"fetched_at": None, "assigned": [], "taught": {}})
        self._saved = json.dumps(self.state, sort_keys=True)

    # ---- helpers -------------------------------------------------------------------------
    def _cooldown_ok(self, name: str, now: datetime) -> bool:
        last = self.state["alerts"].get(f"{name}_alerted")
        return last is None or datetime.fromisoformat(last) <= now - ALERT_COOLDOWN

    def _warn(self, name: str, text: str, now: datetime) -> None:
        if self._cooldown_ok(name, now):
            self._send(text, now)
            self.state["alerts"][f"{name}_alerted"] = _ts(now)

    def _send(self, text: str, now: datetime) -> None:
        silent = is_quiet(now)
        if not self.notifier.send(text, silent=silent):
            time.sleep(5)
            self.notifier.send(text, silent=silent)

    def save(self) -> None:
        current = json.dumps(self.state, sort_keys=True)
        if current == self._saved:
            return
        try:
            self.store.save(self.state)
            self._saved = current
        except Exception as exc:
            print(f"state: save failed ({exc.__class__.__name__}), will retry next check")

    # ---- schedule fit --------------------------------------------------------------------
    def _refresh_dash_schedule(self, now: datetime) -> None:
        dash = self.state["dash"]
        last = dash.get("fetched_at")
        if last and datetime.fromisoformat(last) > now - SCHEDULE_EVERY:
            return
        today = (now + SGT_OFFSET).date()
        try:
            assigned = fetch_weeks(self.client, today)
        except LayoutChanged as exc:
            print("my-schedule: layout not recognised")
            self._warn("schedule_layout", "⚠️ <b>ms-notif2:</b> couldn't read My Class Schedule "
                       f"(page looks different), so 🔁 / Dash clashes may be out of date.\n<i>{str(exc)[:200]}</i>", now)
            return
        taught = dash.setdefault("taught", {})
        for a in assigned:
            if a.relief:
                sig = signature(a.class_name, a.centre, a.start)
                taught[sig] = max(taught.get(sig, ""), a.start.date().isoformat())
        cutoff = (today - timedelta(days=400)).isoformat()
        for sig in [s for s, d in taught.items() if d < cutoff]:
            del taught[sig]
        dash["assigned"] = [{"start": _ts(a.start), "end": _ts(a.end), "class_name": a.class_name,
                             "centre": a.centre, "relief": a.relief}
                            for a in assigned if a.end.date() >= today]
        dash["fetched_at"] = _ts(now)
        print(f"my-schedule: {len(dash['assigned'])} upcoming lessons, {len(taught)} relief classes covered")

    def _events(self, start: datetime, end: datetime) -> list[Event]:
        events = self.calendar.events(start, end)
        for a in self.state["dash"].get("assigned", []):
            s, e = datetime.fromisoformat(a["start"]), datetime.fromisoformat(a["end"])
            if s < end and e > start:
                events.append(Event(f'{a["class_name"]} (Dash)', s, e, a["centre"], source="dash"))
        return events

    def _pending(self) -> list[Lesson]:
        return [_lesson(r) for r in self.state["lessons"].values()
                if r.get("status") == "pending" and "gone_since" not in r]

    def _annotate(self, alerts: list[ClassAlert]) -> None:
        lessons = [i.lesson for a in alerts for i in a.items]
        start = min(datetime.fromisoformat(les.start) for les in lessons) - timedelta(days=1)
        end = max(datetime.fromisoformat(les.end) for les in lessons) + timedelta(days=1)
        events = self._events(start, end)
        pending = self._pending()
        taught_sigs = self.state["dash"].get("taught", {})
        for a in alerts:
            a.fits = [evaluate(i.lesson, events, self.cfg, pending) for i in a.items]
            a.taught = any(signature(i.lesson.class_name, i.lesson.centre, datetime.fromisoformat(i.lesson.start))
                           in taught_sigs for i in a.items)
            a.rank = rank(a.fits, a.taught or bool(a.pending))

    def _backlog(self, now: datetime) -> list[str]:
        today = (now + SGT_OFFSET).isoformat()
        open_items = [NewItem(_lesson(r), new_class=True) for r in self.state["lessons"].values()
                      if r.get("status") == "open" and "gone_since" not in r and r["start"] > today]
        if not open_items:
            return []
        alerts = group_new(open_items, self.state)
        self._annotate(alerts)
        starred = [a for a in alerts if a.starred]
        print(f"backlog: {len(starred)} of {len(alerts)} open classes fit")
        return format_backlog(alerts, self.cfg)

    # ---- one check -----------------------------------------------------------------------
    def check(self, now: datetime | None = None, force_full: bool = False) -> None:
        now = now or utcnow()
        st = self.state
        alerts = st["alerts"]
        failing_since = alerts.get("session_failing_since")
        if failing_since and not force_full:
            last_try = alerts.get("session_last_try")
            if last_try and datetime.fromisoformat(last_try) > now - FAILING_RETRY_EVERY:
                print(f"[{now:%H:%M}Z] session expired earlier; next retry later")
                return

        before = self.client.requests_made
        new: list[NewItem] = []
        log = []
        baselines_before = dict(st["baseline_done"])
        try:
            total, pages, page1 = relief_page1(self.client)
            prev_total = st["relief_total"]
            delta = "" if prev_total is None else f" ({total - prev_total:+d})"
            log.append(f"relief N={total}{delta}")
            if (force_full or prev_total != total or not st["baseline_done"]["relief"]
                    or due(st, "relief", RELIEF_FULL_EVERY, now)):
                lessons, complete = relief_full(self.client, pages, page1)
                found = apply_scan(st, "relief", lessons, complete, now)
                new += found
                classes = len({les.class_code for les in lessons})
                log.append(f"full {pages}p classes={classes} lessons={len(lessons)} new={len(found)}"
                           + ("" if complete else " INCOMPLETE"))
            st["relief_total"] = total

            if force_full or not st["baseline_done"]["makeup"] or due(st, "makeup", MAKEUP_FULL_EVERY, now):
                lessons, complete = makeup_full(self.client)
                found = apply_scan(st, "makeup", lessons, complete, now)
                new += found
                classes = len({les.class_code for les in lessons})
                log.append(f"| makeup classes={classes} lessons={len(lessons)} new={len(found)}"
                           + ("" if complete else " INCOMPLETE"))

            if self.calendar is not None:
                self._refresh_dash_schedule(now)

            if failing_since:
                self._send("✅ Dash session is working again. Alerts are back on.", now)
            for key in ("session_failing_since", "session_last_try", "session_alerted"):
                alerts.pop(key, None)
        except SessionExpired:
            alerts["session_last_try"] = _ts(now)
            alerts.setdefault("session_failing_since", _ts(now))
            print(f"[{now:%H:%M}Z] session expired (sign-in page)")
            self._warn("session", "⚠️ <b>ms-notif2:</b> Dash sent the sign-in page, so the session cookie has "
                       "probably expired. Alerts are paused until <code>MS_COOKIE</code> is refreshed "
                       "(I'll retry every 30 min).", now)
            self.save()
            return
        except LayoutChanged as exc:
            print(f"[{now:%H:%M}Z] page layout not recognised")
            self._warn("layout", "⚠️ <b>ms-notif2:</b> the bulletin page looks different from what I expect, "
                       "so I can't read it right now. The parser probably needs updating.\n"
                       f"<i>{type(exc).__name__}: {str(exc)[:200]}</i>", now)
            self.save()
            return
        except requests.RequestException as exc:
            print(f"[{now:%H:%M}Z] network error ({exc.__class__.__name__}), skipping this check")
            return

        class_alerts = group_new(new, st)
        calendar_ok = self.calendar is not None
        if class_alerts and calendar_ok:
            try:
                self._annotate(class_alerts)
                log.append(f"starred={sum(a.starred for a in class_alerts)}")
            except Exception as exc:
                calendar_ok = False
                for a in class_alerts:
                    a.fits, a.rank, a.taught = None, None, False
                print(f"calendar: {exc.__class__.__name__}")
                self._warn("calendar", "⚠️ <b>ms-notif2:</b> couldn't read your Google Calendar "
                           f"({exc.__class__.__name__}), so this alert has no ⭐.", now)
        print(f"[{now:%H:%M}Z] {' '.join(log)} req={self.client.requests_made - before}")

        newly_baselined = [k for k, done in st["baseline_done"].items() if done and not baselines_before[k]]
        if newly_baselined:
            self._send(self._hello(newly_baselined), now)
        for message in format_alerts(class_alerts):
            self._send(message, now)

        if (calendar_ok and self.calendar is not None and not st.get("backlog_sent")
                and all(st["baseline_done"].values())):
            try:
                for message in self._backlog(now):
                    self._send(message, now)
                st["backlog_sent"] = _ts(now)
            except Exception as exc:
                print(f"backlog: {exc.__class__.__name__}")
        self.save()

    def _hello(self, kinds: list[str]) -> str:
        parts = []
        for kind in kinds:
            lessons = [v for v in self.state["lessons"].values() if v["kind"] == kind]
            classes = {v["class_code"] for v in lessons}
            label = "relief" if kind == "relief" else "make-up"
            parts.append(f"{len(classes)} {label} classes ({len(lessons)} lessons)")
        return ("👋 <b>ms-notif2 v2</b> is now watching the bulletin: " + " and ".join(parts)
                + ".\nYou'll get a message whenever a new class or week is posted.")


def build_calendar(cfg: Config):
    sa = os.environ.get("GCAL_SA_JSON", "").strip()
    if not sa:
        return None
    if not cfg.calendar_ids:
        print("calendar: GCAL_SA_JSON is set but CONFIG_JSON has no calendar_ids; ⭐ disabled")
        return None
    from .gcal import GoogleCalendar
    return GoogleCalendar(sa, cfg.calendar_ids)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="watcher", description="Watch the Mind Stretcher bulletin.")
    p.add_argument("--loop-minutes", type=float, default=0, help="keep checking for this long (0 = one check)")
    p.add_argument("--interval-minutes", type=float, default=6)
    p.add_argument("--dry-run", action="store_true", help="don't send Telegram messages or save state")
    p.add_argument("--show-messages", action="store_true", help="print would-be messages (local use only)")
    p.add_argument("--state-file", help="use a local unencrypted state file instead of the state branch")
    p.add_argument("--force-full", action="store_true", help="full scan on the first check")
    p.add_argument("--resend-backlog", action="store_true", help="send the 'open classes that fit' list again")
    args = p.parse_args(argv)

    cookie = os.environ.get("MS_COOKIE", "").strip()
    if not cookie:
        print("MS_COOKIE is not set")
        return 1
    if args.state_file:
        store = FileStore(args.state_file)
    else:
        key = os.environ.get("STATE_KEY", "").strip()
        if not key:
            print("STATE_KEY is not set")
            return 1
        store = GitBranchStore(key)
    if args.dry_run:
        store.save = lambda state: None
        notifier = Recorder(show=args.show_messages)
    else:
        token, chat = os.environ.get("TELE_BOT_TOKEN", ""), os.environ.get("TELE_CHAT_ID", "")
        if not token or not chat:
            print("TELE_BOT_TOKEN / TELE_CHAT_ID are not set")
            return 1
        notifier = Telegram(token, chat)

    raw_cfg = os.environ.get("CONFIG_JSON", "").strip()
    cfg = Config.from_dict(json.loads(raw_cfg)) if raw_cfg else Config()
    calendar = build_calendar(cfg)
    print(f"schedule fit: {'on' if calendar else 'off'}")

    watcher = Watcher(Client(cookie), store, notifier, cfg, calendar)
    if args.resend_backlog:
        watcher.state.pop("backlog_sent", None)
    end = time.monotonic() + args.loop_minutes * 60
    interval = args.interval_minutes * 60
    first = True
    while True:
        started = time.monotonic()
        watcher.check(force_full=args.force_full and first)
        first = False
        if time.monotonic() + interval > end:
            break
        time.sleep(max(0.0, started + interval - time.monotonic()))
    if isinstance(notifier, Recorder):
        print(f"dry run: {len(notifier.sent)} message(s) would have been sent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
