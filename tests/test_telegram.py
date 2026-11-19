"""The real Telegram client, with the network call replaced (the other tests use a fake Telegram)."""
import pytest

import watcher.notify as notify


class FakeResponse:
    def __init__(self, result):
        self.status_code = 200
        self._result = result

    def json(self):
        return {"ok": True, "result": self._result}


@pytest.fixture
def calls(monkeypatch):
    seen = []

    def fake_post(url, json, timeout):
        seen.append((url.rsplit("/", 1)[1], json, timeout))
        method = url.rsplit("/", 1)[1]
        return FakeResponse({"message_id": 42} if method == "sendMessage" else
                            [{"update_id": 7}] if method == "getUpdates" else True)

    monkeypatch.setattr(notify.requests, "post", fake_post)
    return seen


def test_updates_long_poll(calls):
    tg = notify.Telegram("TOKEN", "123")
    assert tg.updates(5, timeout=50) == [{"update_id": 7}]
    method, payload, http_timeout = calls[0]
    assert method == "getUpdates"
    assert payload == {"timeout": 50, "allowed_updates": ["callback_query"], "offset": 5}
    assert http_timeout == 65


def test_send_with_buttons_and_reply(calls):
    tg = notify.Telegram("TOKEN", "123")
    kb = [[{"text": "✅ Bid", "callback_data": "bid:abc"}]]
    assert tg.send("hi", silent=True, keyboard=kb, reply_to=9) == 42
    method, payload, _ = calls[0]
    assert method == "sendMessage"
    assert payload["chat_id"] == "123" and payload["disable_notification"] is True
    assert payload["reply_markup"] == {"inline_keyboard": kb}
    assert payload["reply_parameters"]["message_id"] == 9


def test_edit_and_answer(calls):
    tg = notify.Telegram("TOKEN", "123")
    tg.edit_keyboard(42, [[{"text": "Skipped", "callback_data": "noop:"}]])
    tg.answer("cb1", "On it")
    assert tg.edit_text(43, "✅ <b>Bid placed</b>")
    assert [c[0] for c in calls] == ["editMessageReplyMarkup", "answerCallbackQuery", "editMessageText"]
    assert calls[1][1] == {"callback_query_id": "cb1", "text": "On it"}
    assert calls[2][1]["message_id"] == 43 and calls[2][1]["parse_mode"] == "HTML"
