"""Public assessment uses OPENAI_API_KEY even when inbound auto-replies are off."""

from types import SimpleNamespace

from acquisition.services.assessment_chat_service import ASSESSMENT_MODEL, AssessmentChatError, AssessmentChatService


class _Response:
    output_text = '{"assistant_message": "What is getting in the way right now?"}'
    output = []


class _Client:
    def __init__(self) -> None:
        self.calls = []

        class _Responses:
            def __init__(self, outer: "_Client") -> None:
                self._outer = outer

            def create(self, **kwargs):
                self._outer.calls.append(kwargs)
                return _Response()

        self.responses = _Responses(self)


def _service(api_key: str, client: _Client | None = None) -> AssessmentChatService:
    svc = AssessmentChatService.__new__(AssessmentChatService)
    svc.db = None
    svc.settings = SimpleNamespace(openai_api_key=api_key)
    svc._openai_client = client
    return svc


def test_assessment_runs_when_reply_provider_is_not_openai():
    client = _Client()
    svc = _service("sk-test", client)
    text = svc._call_responses([{"role": "user", "content": "We close books slowly."}])
    assert "getting in the way" in text
    assert client.calls[0]["model"] == ASSESSMENT_MODEL


def test_assessment_is_unavailable_without_an_api_key():
    svc = _service("")
    try:
        svc._call_responses([{"role": "user", "content": "hi"}])
    except AssessmentChatError as exc:
        assert exc.code == "no_openai"
        assert exc.http_status == 503
    else:
        raise AssertionError("expected AssessmentChatError")
