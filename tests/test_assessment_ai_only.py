"""The public assessment has no static qualified/unqualified demo path."""

import importlib.util
from pathlib import Path

from acquisition.api.assessment import AssessmentMessageRequest


ROOT = Path(__file__).resolve().parents[1]


def test_demo_static_module_is_gone():
    assert importlib.util.find_spec("acquisition.services.demo_static_assessment") is None
    assert not (ROOT / "acquisition/data/demo_static_assessment").exists()


def test_assessment_chat_has_no_demo_switch():
    html = (ROOT / "acquisition/templates/assessment/chat.html").read_text(encoding="utf-8")
    assert "demo_static" not in html
    assert "assess-mode-dot" not in html
    assert "MODE_CYCLE" not in html
    assert "demoMode" not in html


def test_message_request_has_no_static_mode():
    assert "demo_static" not in AssessmentMessageRequest.model_fields
