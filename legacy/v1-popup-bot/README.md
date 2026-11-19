# v1: notification popup checker (archived)

This folder keeps the original bot as it was when it was retired on 19 Nov 2026. These files are **not run**
any more: the workflow lives here instead of `.github/workflows/`, so GitHub ignores it.

- `check_notifications.py`: loaded `https://dash.mindstretcher.com/notifications/popup/` with the session cookie,
  read `data-unread-count`, and sent a Telegram message if it went up. It also sent one "cookie expired"
  warning per failure streak.
- `check-notifications.yml`: ran it every 5 minutes and committed `last_count.txt` back to `main`.
- `last_count.txt`: the last unread count it saw.
- `README-original.md`: the original README.

It stopped being useful when Mind Stretcher removed in-app relief / make-up notifications (~13 Nov 2026).
The exact last version is also tagged [`v1-popup-bot`](../../../../tree/v1-popup-bot).

See the main [README](../../README.md#history-v1--v2) for the full story and what v2 changed.
