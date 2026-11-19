import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

from .core import apply_scan, due
from .fetch import Client, makeup_full, relief_full, relief_page1
from .notify import Recorder, Telegram, format_new, is_quiet
from .parse import LayoutChanged, SessionExpired
from .state import FileStore, GitBranchStore

RELIEF_FULL_EVERY = timedelta(minutes=90)
MAKEUP_FULL_EVERY = timedelta(minutes=60)
ALERT_COOLDOWN = timedelta(hours=3)
FAILING_RETRY_EVERY = timedelta(minutes=30)


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _ts(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


class Watcher:
    def __init__(self, client, store, notifier):
        self.client = client
        self.store = store
        self.notifier = notifier
        self.state = store.load()
        self._saved = json.dumps(self.state, sort_keys=True)

    def _cooldown_ok(self, name: str, now: datetime) -> bool:
        last = self.state["alerts"].get(f"{name}_alerted")
        return last is None or datetime.fromisoformat(last) <= now - ALERT_COOLDOWN

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
        new = []
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

            if failing_since:
                self._send("✅ Dash session is working again. Alerts are back on.", now)
            for key in ("session_failing_since", "session_last_try", "session_alerted"):
                alerts.pop(key, None)
        except SessionExpired:
            alerts["session_last_try"] = _ts(now)
            alerts.setdefault("session_failing_since", _ts(now))
            print(f"[{now:%H:%M}Z] session expired (sign-in page)")
            if self._cooldown_ok("session", now):
                self._send("⚠️ <b>ms-notif2:</b> Dash sent the sign-in page, so the session cookie has "
                           "probably expired. Alerts are paused until <code>MS_COOKIE</code> is refreshed "
                           "(I'll retry every 30 min).", now)
                alerts["session_alerted"] = _ts(now)
            self.save()
            return
        except LayoutChanged as exc:
            print(f"[{now:%H:%M}Z] page layout not recognised")
            if self._cooldown_ok("layout", now):
                self._send(f"⚠️ <b>ms-notif2:</b> the bulletin page looks different from what I expect, "
                           f"so I can't read it right now. The parser probably needs updating.\n"
                           f"<i>{type(exc).__name__}: {str(exc)[:200]}</i>", now)
                alerts["layout_alerted"] = _ts(now)
            self.save()
            return
        except requests.RequestException as exc:
            print(f"[{now:%H:%M}Z] network error ({exc.__class__.__name__}), skipping this check")
            return

        print(f"[{now:%H:%M}Z] {' '.join(log)} req={self.client.requests_made - before}")

        newly_baselined = [k for k, done in st["baseline_done"].items() if done and not baselines_before[k]]
        if newly_baselined:
            self._send(self._hello(newly_baselined), now)
        if new:
            for message in format_new(new, st):
                self._send(message, now)
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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="watcher", description="Watch the Mind Stretcher bulletin.")
    p.add_argument("--loop-minutes", type=float, default=0, help="keep checking for this long (0 = one check)")
    p.add_argument("--interval-minutes", type=float, default=6)
    p.add_argument("--dry-run", action="store_true", help="don't send Telegram messages or save state")
    p.add_argument("--show-messages", action="store_true", help="print would-be messages (local use only)")
    p.add_argument("--state-file", help="use a local unencrypted state file instead of the state branch")
    p.add_argument("--force-full", action="store_true", help="full scan on the first check")
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

    watcher = Watcher(Client(cookie), store, notifier)
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
