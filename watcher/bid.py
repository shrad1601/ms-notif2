"""Submitting "I'm Interested" on Dash. Only ever called after an explicit tap in Telegram."""
from .fetch import BASE, MAKEUP_URL, RELIEF_URL
from .parse import csrf_token

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


def makeup_bid_listed(client, class_code: str) -> bool:
    """Make-up bids leave the Available list; confirm the class now shows under My Bids."""
    return class_code in client.get(MAKEUP_MY_BIDS_URL)
