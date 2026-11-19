# ms-notif2: Mind Stretcher bulletin watcher

Watches the relief and Holiday Make-Up bulletin on Dash (`dash.mindstretcher.com`) and sends a Telegram
message when a new class, or a new week of an existing class, is posted. Runs on GitHub Actions, so it keeps
working when my laptop is off.

This is **v2**. v1 (a notification-popup checker) is archived in [`legacy/v1-popup-bot/`](legacy/v1-popup-bot/).
See [History](#history-v1--v2) for why it was replaced.

## How it works

One workflow, [`watch.yml`](.github/workflows/watch.yml), starts every hour and keeps checking every ~6 minutes
for ~54 minutes:

| Step | When | Requests |
|---|---|---|
| **Quick check:** read the `Relief Cover (N)` total on relief page 1 | every check (~6 min) | 1 |
| **Full relief scan:** all pages (~14), max 3 at a time | when N changes, and at least every 90 min | ~14 |
| **Full make-up scan:** follow "More classes" (~5 pages) | at least every 60 min | ~5 |

That's roughly **600–700 page loads a day**.

- **A brand-new relief class** raises N, so it's alerted on the next check (usually within 5–10 min).
- **A new week added to an existing class** doesn't change N (N counts classes, not lessons), so it's caught by
  the 90-minute full scan.

Each full scan is compared with the previous one, and only lessons never seen before are alerted:

- 🆕 **new class:** every lesson listed
- ➕ **new week(s)** on a class already on the bulletin, plus "🔁 You've already bid on: …" if you have
- ↩️ **back on bulletin:** a lesson that disappeared for over 24 h and came back

A lesson that briefly drops out of one scan (pages shifting while it scans) isn't alerted again. Neither is
everything after the bot was offline for a while.

Between 11pm and 7am (SGT) messages are sent **silently** (no sound).

### Schedule fit (⭐ / 🔁)
When `GCAL_SA_JSON` and `CONFIG_JSON` are set, every new lesson is checked before alerting.

- **Hard rules** (from `CONFIG_JSON`): centres that are too far, and the latest end time per weekday.
- **Clashes with Google Calendar,** read through a read-only service account:
  - overlapping a busy event → ❌
  - timed events marked *Free* → ignored
  - **trips** (all-day or 24 h+ events, even if marked Free, which Google does by default) → block every day
    they touch, including the day you leave and the day you're back
  - events whose title contains one of your `soft_keywords` (e.g. a commitment you'd be willing to cancel) → ⚠️
    flag, not blocking
  - an event at another place within 30 min before/after → ⚠️ with the gap, so you can judge the travel
- **Clashes with My Class Schedule on Dash:** lessons already assigned to you, read once a day (~11 page loads).
  If a class is both on Dash and in your calendar, it's counted once.
- **Overlap with a lesson you've already bid on** → ❌.

**Tags**
- **🔗 back-to-back:** same centre, gap ≤30 min on weekdays, ≤45 min on weekends and for make-ups.
  Either next to something already in your schedule, or next to another open lesson that also fits.
- **🔁 covered before:** matches (course, centre, weekday, time) a relief lesson on your My Class Schedule.
- **✋** you've already bid on other weeks of this class.

**Ranking.** Back-to-back is what counts; standalone classes aren't wanted.
1. 🔗 and (🔁 or ✋)
2. 🔗
3. ✋ standalone
4. fits but standalone
5. doesn't fit

Groups 1–3 get ⭐ and their own message, with ✅/❌ for each week. Everything else goes into one
"Also new (standalone, or doesn't fit)" summary, with the reason.

The first time this is switched on (or when run with `resend_backlog`), you get the **open classes that fit**
list, one message each for Relief and Holiday Make-Up. ⭐ Best bets come first, then the standalone ones, in date order.

### Safety and privacy (this repo is public)
- The session cookie is only sent as a request header. It's never logged or stored.
- Logs show counts only (`relief N=268 (+1) full 14p lessons=431 new=2 req=15`), never class details.
- What's been seen is kept in `state.enc`, **encrypted** (Fernet, `STATE_KEY`), as the only file on the separate
  `state` branch. That branch is force-pushed as a single commit, so it has no history.
- If Dash returns the sign-in page, you get **one** Telegram warning (repeated at most every 3 h). It retries
  every 30 min and tells you when it works again. If the page layout changes, you also get a warning instead
  of silence.

## Setup

Repository secrets (Settings → Secrets and variables → Actions):

| Secret | What |
|---|---|
| `MS_COOKIE` | Dash session cookie (see below) |
| `TELE_BOT_TOKEN` | Telegram bot token from @BotFather |
| `TELE_CHAT_ID` | Your Telegram chat id |
| `STATE_KEY` | Fernet key for the state file. Generate once: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `GCAL_SA_JSON` | *(optional, for ⭐)* JSON key of a Google service account that your calendars are shared with ("See all event details") |
| `CONFIG_JSON` | *(optional, for ⭐)* schedule rules, e.g. `{"calendar_ids": ["you@gmail.com"], "too_far": ["Some Centre"], "latest_end": {"Tue": "17:00"}, "soft_keywords": ["side job"]}`. Kept as a secret because it describes your availability. |

### Refreshing the cookie
Dash logins use an emailed 2FA code, so the bot can't log in by itself. When you get the "session expired"
message:
1. Log in to Dash in Chrome, then open DevTools → Network and reload.
2. Right-click any `dash.mindstretcher.com` request → Copy → **Copy as cURL**.
3. Take the value after `-b '…'` (the cookie string) and run
   `gh secret set MS_COOKIE --repo shrad1601/ms-notif2`, pasting it when prompted.

### Running it by hand
- Actions tab → **Watch Mind Stretcher bulletin** → Run workflow.
  - `loop_minutes`: `0` for one check
  - `dry_run`: nothing is sent or saved
  - `force_full`: scan every page on the first check
- Pause: `gh workflow disable watch.yml --repo shrad1601/ms-notif2` (re-enable with `enable`).
- Locally (dry run, messages printed, unencrypted local state):
  ```bash
  pip install -r requirements.txt
  MS_COOKIE='…' python -m watcher --dry-run --show-messages --state-file state.local.json --force-full
  ```
- Tests: `pip install pytest && python -m pytest tests`. These use made-up pages that mirror Dash's markup;
  no real class data is committed.

## Roadmap
- ~~**Phase 2:** ⭐ schedule fit, 🔁 covered before, ranking, backlog~~ (done, see [Schedule fit](#schedule-fit--))
- **Phase 3:** tap-to-bid. Telegram **[Bid]** buttons; the bot re-checks the calendar and bids only after my tap.
- **Phase 4:** track bid timing vs outcome (Accepted/Closed), with weekly stats to tune how often it checks.

## History: v1 → v2

### v1: notification popup checker (Jul–Nov 2026)
- `check_notifications.py` loaded `/notifications/popup/` every 5 min with the session cookie, read
  `data-unread-count`, and sent a Telegram message when the number went up.
- A companion Tampermonkey script did the same in the browser every 20 s, with a beep and a desktop notification.
- **What went wrong along the way**
  - **Minutes used up:** the repo started out private, so 5-minute runs (each billed as a full minute) used up
    the Actions minutes. Making the repo public fixed this.
  - **Expired cookie:** the session cookie expired once and had to be refreshed by hand.
  - **Thin coverage:** it only ever knew *that* something was unread, not *what*. In-app alerts also covered only
    a small slice of the bulletin (≈44 relief alerts in 2.5 weeks vs ≈430 open relief lessons).
- **Why it was retired:** around **13 Nov 2026** Mind Stretcher stopped sending in-app relief / make-up
  notifications (only a 7am daily summary email is left), so v1 could no longer see new classes. It was paused
  on 19 Nov 2026 and archived.
- Code: tag [`v1-popup-bot`](../../tree/v1-popup-bot) and [`legacy/v1-popup-bot/`](legacy/v1-popup-bot/).

### v2: what's better
| | v1 | v2 |
|---|---|---|
| Source | notification popup (now gone) | the bulletin itself, relief and make-up |
| What you're told | "unread count went up" | class, centre, class code, every new lesson's date/time/week, bidding deadline |
| New weeks on existing classes | no | yes (➕), including which weeks you've already bid on |
| Speed | depended on Dash sending a notification | new relief classes in ~5–10 min (steady 6-min checks instead of drifting cron) |
| Traffic | constant popup polling (bot + Tampermonkey every 20 s) | ~600–700 normal page loads/day, mostly 1 page per check |
| State | `last_count.txt`, plain text in the repo | encrypted on a separate branch |
| Failures | silent | Telegram warning for expired cookie or changed layout, plus recovery message |
| Overnight | always rang | silent 11pm–7am |
| Tests | none | parser, diff and message tests with fake pages |
