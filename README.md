# Mind Stretcher Notification Alert

Checks the Mind Stretcher relief bulletin notification endpoint every 5 minutes
and sends a Telegram message if the unread count goes up. Runs entirely on
GitHub Actions, so it works even when your laptop/phone are off.

## How it works

- `check_notifications.py` calls `https://dash.mindstretcher.com/notifications/popup/`
  using your saved session cookie, and reads the `data-unread-count` value from
  the response.
- It compares that to the last known count (stored in `last_count.txt`, committed
  back to the repo after each run).
- If the count went up, it sends a Telegram message via the Bot API.
- The workflow (`.github/workflows/check-notifications.yml`) runs this on a
  5-minute schedule, and can also be triggered manually from the Actions tab
  ("Run workflow" button) for testing.

## Required GitHub secrets

Set these under Settings -> Secrets and variables -> Actions:

- `MS_COOKIE` — your dash.mindstretcher.com session cookie
- `TELE_BOT_TOKEN` — Telegram bot token from BotFather
- `TELE_CHAT_ID` — your Telegram chat ID

## Known limitation: cookie expiry

Session cookies expire eventually (exact timing unknown, could be days or weeks).
When that happens, requests will fail auth and the script will log a warning
instead of crashing (it just skips that run silently). If you stop getting
alerts for an unusually long stretch with no real gap in notifications, that's
the likely cause; re-extract the cookie from your browser (same DevTools ->
Network -> popup/ -> Headers -> Cookie steps as before) and update the
`MS_COOKIE` secret.

## Testing manually

Go to the repo's Actions tab -> "Check Mind Stretcher Notifications" workflow ->
"Run workflow" button, to trigger a run on demand without waiting for the
5-minute schedule.
