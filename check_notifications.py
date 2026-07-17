import os
import re
import sys
import urllib.request
import urllib.parse

STATE_FILE = "last_count.txt"
NOTIF_URL = "https://dash.mindstretcher.com/notifications/popup/"


def get_env(name):
    value = os.environ.get(name)
    if not value:
        print(f"ERROR: missing required env var {name}")
        sys.exit(1)
    return value


def fetch_unread_count(cookie):
    req = urllib.request.Request(
        NOTIF_URL,
        headers={
            "Cookie": cookie,
            "User-Agent": "Mozilla/5.0 (compatible; NotifyBot/1.0)",
            "Accept": "text/html",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = resp.read().decode("utf-8", errors="replace")

    # Look for data-unread-count="N" in the response HTML
    match = re.search(r'data-unread-count="(\d+)"', body)
    if not match:
        print("WARNING: could not find data-unread-count in response.")
        print("First 500 chars of response for debugging:")
        print(body[:500])
        return None

    return int(match.group(1))


def read_last_count():
    if not os.path.exists(STATE_FILE):
        return None
    with open(STATE_FILE, "r") as f:
        content = f.read().strip()
        return int(content) if content.isdigit() else None


def write_last_count(count):
    with open(STATE_FILE, "w") as f:
        f.write(str(count))


def send_telegram(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=20) as resp:
        print("Telegram response status:", resp.status)


def main():
    cookie = get_env("MS_COOKIE")
    bot_token = get_env("TELE_BOT_TOKEN")
    chat_id = get_env("TELE_CHAT_ID")

    current_count = fetch_unread_count(cookie)
    if current_count is None:
        # Could mean the cookie expired (logged out) or the page structure changed.
        # Don't crash the whole workflow, just log and exit cleanly.
        print("Could not determine unread count this run. Skipping.")
        return

    last_count = read_last_count()
    print(f"Last known count: {last_count} | Current count: {current_count}")

    if last_count is not None and current_count > last_count:
        send_telegram(
            bot_token,
            chat_id,
            f"New Mind Stretcher notification! Unread count: {current_count}",
        )
        print("Alert sent.")
    else:
        print("No new notifications.")

    write_last_count(current_count)


if __name__ == "__main__":
    main()
