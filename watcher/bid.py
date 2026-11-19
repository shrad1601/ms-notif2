"""Submitting "I'm Interested" / "Withdraw" on Dash. Only ever called after an explicit tap in Telegram."""
from urllib.parse import urljoin

from .fetch import BASE, MAKEUP_URL, RELIEF_URL
from .parse import csrf_token, parse_makeup_bids_page

MAX_BID_PAGES = 15

RELIEF_FIELDS = [("category", "relief-cover"), ("programme", "all"), ("tab", "my-bids")]
MAKEUP_FIELDS = [("category", "holiday-make-up"), ("programme", "all"), ("tab", "my-bids")]
MAKEUP_MY_BIDS_URL = f"{BASE}/teacher/bulletin/?category=holiday-make-up&programme=all&tab=my-bids"


class BidError(Exception):
    pass


def _token(client, page_url: str) -> str:
    token = csrf_token(client.get(page_url)) or getattr(client, "csrf_cookie", None)
    if not token:
        raise BidError("couldn't find the form's security token on the bulletin page")
    return token


def _submit(client, url: str, data: list[tuple[str, str]], referer: str) -> None:
    status, _ = client.post(url, data, referer)
    if status == 403:
        raise BidError("Dash refused the form (HTTP 403)")
    if status >= 400:
        raise BidError(f"Dash returned HTTP {status}")


def bid_relief(client, request_ids: list[str], all_lessons: bool) -> None:
    """all_lessons=True uses "Interested in all lessons" (only when request_ids are every open lesson on the card)."""
    token = _token(client, RELIEF_URL)
    if all_lessons and len(request_ids) > 1:
        data = [("csrfmiddlewaretoken", token), *RELIEF_FIELDS,
                *[("assignment_request_ids", rid) for rid in request_ids]]
        _submit(client, f"{BASE}/teacher/bulletin/bid-all/", data, RELIEF_URL)
        return
    for rid in request_ids:
        _submit(client, f"{BASE}/teacher/bulletin/{rid}/bid/",
                [("csrfmiddlewaretoken", token), *RELIEF_FIELDS], RELIEF_URL)


def bid_makeup(client, request_id: str) -> None:
    token = _token(client, MAKEUP_URL)
    _submit(client, f"{BASE}/teacher/bulletin/permanent/{request_id}/bid/",
            [("csrfmiddlewaretoken", token), *MAKEUP_FIELDS], MAKEUP_URL)


def makeup_bids(client, wanted: set[str]) -> dict[str, tuple[str, str | None]]:
    """Find these class codes under Holiday Make-Up → My Bids (several pages): {code: (status, withdraw id)}."""
    found: dict[str, tuple[str, str | None]] = {}
    url: str | None = MAKEUP_MY_BIDS_URL
    seen = set()
    while url and url not in seen and len(seen) < MAX_BID_PAGES and not wanted <= found.keys():
        seen.add(url)
        bids, next_href = parse_makeup_bids_page(client.get(url))
        found.update({code: v for code, v in bids.items() if code in wanted})
        url = urljoin(BASE, next_href) if next_href else None
    return found


def withdraw_relief(client, withdraw_id: str) -> None:
    token = _token(client, RELIEF_URL)
    _submit(client, f"{BASE}/teacher/bulletin/{withdraw_id}/withdraw/",
            [("csrfmiddlewaretoken", token), *RELIEF_FIELDS], RELIEF_URL)


def withdraw_makeup(client, withdraw_id: str) -> None:
    token = _token(client, MAKEUP_MY_BIDS_URL)
    _submit(client, f"{BASE}/teacher/bulletin/permanent/{withdraw_id}/withdraw/",
            [("csrfmiddlewaretoken", token), *MAKEUP_FIELDS], MAKEUP_MY_BIDS_URL)
