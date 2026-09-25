"""The Claude backend is exercised against a fake client: no network, no key."""

from types import SimpleNamespace

import pytest

from edi_triage.llm import ClassificationOutput, ClaudeTriageLLM
from edi_triage.models import FailureCategory


class FakeMessages:
    def __init__(self, parsed, stop_reason="end_turn"):
        self.parsed, self.stop_reason, self.calls = parsed, stop_reason, []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            parsed_output=self.parsed,
            stop_reason=self.stop_reason,
            usage=SimpleNamespace(input_tokens=1_000, output_tokens=200),
        )


def make(parsed, stop_reason="end_turn"):
    messages = FakeMessages(parsed, stop_reason)
    return ClaudeTriageLLM(model="claude-opus-5", client=SimpleNamespace(messages=messages)), messages


def test_classify_uses_structured_output_and_meters_cost(incident):
    expected = ClassificationOutput(
        category=FailureCategory.CERTIFICATE_EXPIRY, confidence=0.9, rationale="x"
    )
    llm, messages = make(expected)
    assert llm.classify(incident("INC-009")) == expected
    call = messages.calls[0]
    assert call["output_format"] is ClassificationOutput
    assert call["thinking"] == {"type": "adaptive"}
    assert llm.meter.calls == 1
    assert llm.meter.cost_usd == pytest.approx((1_000 * 5 + 200 * 25) / 1e6)


def test_refusal_is_surfaced(incident):
    llm, _ = make(None, stop_reason="refusal")
    with pytest.raises(RuntimeError, match="refusal"):
        llm.classify(incident("INC-009"))
