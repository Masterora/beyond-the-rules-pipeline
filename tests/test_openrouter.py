import json

from btr_pipeline.openrouter import OpenRouterClient


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
        self.text = json.dumps(payload)
        self.content = self.text.encode()
        self.headers = {"content-type": "application/json"}

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_chat_retries_content_filter_empty_response(monkeypatch):
    responses = iter(
        [
            FakeResponse(
                {
                    "choices": [
                        {"finish_reason": "content_filter", "message": {"content": ""}}
                    ]
                }
            ),
            FakeResponse(
                {
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": '{"ok":true}'}}
                    ]
                }
            ),
        ]
    )
    monkeypatch.setattr("requests.post", lambda *args, **kwargs: next(responses))
    client = OpenRouterClient("test", "test/model")
    assert client.chat_json(system="system", user="user") == {"ok": True}


def test_parse_json_tolerates_wrapping_text():
    value = OpenRouterClient._parse_json('Result follows: {"ok": true}\nDone.')
    assert value == {"ok": True}


def test_parse_json_rejects_non_object():
    try:
        OpenRouterClient._parse_json(json.dumps([1, 2]))
    except TypeError:
        pass
    else:
        raise AssertionError("non-object JSON must be rejected")


def test_parse_api_response_accepts_first_complete_envelope():
    payload = OpenRouterClient._parse_api_response(
        '{"choices":[{"message":{"content":"{}"}}]}\nprovider diagnostic'
    )

    assert payload["choices"][0]["message"]["content"] == "{}"


def test_parse_api_response_rejects_unrequested_stream():
    try:
        OpenRouterClient._parse_api_response('data: {"choices":[]}')
    except ValueError as exc:
        assert "streaming" in str(exc)
    else:
        raise AssertionError("unrequested streaming response must be rejected")
