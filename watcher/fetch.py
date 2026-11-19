import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin

import requests

from .parse import (
    LayoutChanged,
    Lesson,
    SessionExpired,
    check_logged_in,
    parse_makeup_page,
    parse_relief_page,
)

BASE = "https://dash.mindstretcher.com"
RELIEF_URL = f"{BASE}/teacher/bulletin/?category=relief-cover&programme=all&tab=available"
MAKEUP_URL = f"{BASE}/teacher/bulletin/?tab=available&category=holiday-make-up&programme=all"
USER_AGENT = "Mozilla/5.0 (compatible; ms-notif2/2.0; personal bulletin watcher)"
MAX_PARALLEL = 3
MAX_MAKEUP_PAGES = 20


def relief_page_url(n: int) -> str:
    return RELIEF_URL if n == 1 else f"{RELIEF_URL}&relief_page={n}"


class Client:
    def __init__(self, cookie: str, timeout: int = 20):
        self.timeout = timeout
        self.requests_made = 0
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Cookie": cookie,
        })
        # Django accepts the csrftoken cookie value as the form token too; used only if a page has no form
        self.csrf_cookie = next((part.split("=", 1)[1] for part in cookie.split("; ")
                                 if part.startswith("csrftoken=")), None)

    def get(self, url: str) -> str:
        last_exc: Exception | None = None
        for attempt in range(2):
            if attempt:
                time.sleep(5)
            try:
                self.requests_made += 1
                r = self.session.get(url, timeout=self.timeout, allow_redirects=True)
            except requests.RequestException as exc:
                last_exc = exc
                continue
            if r.status_code == 429 or r.status_code >= 500:
                last_exc = requests.HTTPError(f"HTTP {r.status_code}")
                continue
            check_logged_in(r.text, r.url)
            r.raise_for_status()
            return r.text
        raise last_exc  # type: ignore[misc]

    def post(self, url: str, data: list[tuple[str, str]], referer: str) -> tuple[int, str]:
        """Submit a Dash form the way the browser does. Never retried: a bid must not go in twice."""
        self.requests_made += 1
        r = self.session.post(url, data=data, timeout=self.timeout, allow_redirects=True,
                              headers={"Referer": referer, "Origin": BASE})
        check_logged_in(r.text, r.url)
        return r.status_code, r.text


class FixtureClient:
    """Serves saved HTML files instead of hitting the site (offline testing)."""

    def __init__(self, pages: dict[str, str], on_post=None):
        self.pages = pages
        self.requests_made = 0
        self.posts: list[tuple[str, list[tuple[str, str]], str]] = []
        self.on_post = on_post  # tests: callable(url, data) -> (status, html), may update self.pages

    def get(self, url: str) -> str:
        self.requests_made += 1
        if url not in self.pages:
            raise requests.HTTPError(f"no fixture for {url}")
        html = self.pages[url]
        check_logged_in(html, url)
        return html

    def post(self, url: str, data: list[tuple[str, str]], referer: str) -> tuple[int, str]:
        self.requests_made += 1
        self.posts.append((url, data, referer))
        return self.on_post(url, data) if self.on_post else (200, "<html><main>ok</main></html>")


def relief_page1(client) -> tuple[int, int, list[Lesson]]:
    total, _, pages, lessons = parse_relief_page(client.get(relief_page_url(1)))
    return total, pages, lessons


def relief_full(client, pages: int, page1_lessons: list[Lesson]) -> tuple[list[Lesson], bool]:
    """Fetch pages 2..pages in parallel. Returns (all lessons, complete?)."""
    lessons = list(page1_lessons)
    complete = True

    def fetch(n: int):
        return parse_relief_page(client.get(relief_page_url(n)))[3]

    if pages > 1:
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
            futures = [pool.submit(fetch, n) for n in range(2, pages + 1)]
            for fut in futures:
                try:
                    lessons.extend(fut.result())
                except (SessionExpired, LayoutChanged):
                    raise
                except Exception:
                    complete = False
    return _dedupe(lessons), complete


def makeup_full(client) -> tuple[list[Lesson], bool]:
    lessons: list[Lesson] = []
    url: str | None = MAKEUP_URL
    seen_urls: set[str] = set()
    complete = True
    while url and len(seen_urls) < MAX_MAKEUP_PAGES:
        seen_urls.add(url)
        try:
            page_lessons, next_href = parse_makeup_page(client.get(url))
        except (SessionExpired, LayoutChanged):
            raise
        except Exception:
            complete = False
            break
        lessons.extend(page_lessons)
        url = urljoin(BASE, next_href) if next_href else None
        if url in seen_urls:
            break
    if url and len(seen_urls) >= MAX_MAKEUP_PAGES:
        complete = False
    return _dedupe(lessons), complete


def _dedupe(lessons: list[Lesson]) -> list[Lesson]:
    out: dict[str, Lesson] = {}
    for lesson in lessons:
        out.setdefault(lesson.key, lesson)
    return list(out.values())
