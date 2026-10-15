"""Further checks of specific paths: estimator modes, serialisation for the file limit, protection by pattern,
the reported-overshoot rule, and the example. The transcript tests remain the exact contract."""

import asyncio
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RequestUsage

from guarded_clm import BudgetExceededError, ClmContext, ContextLimits, clm_context
from guarded_clm.capability import is_protected
from guarded_clm.context import ContextStore, Entry, parse_candidate, serialize
from guarded_clm.estimate import Calibration, part_text, reported_overshoot, reports_real_usage

ROOT = Path(__file__).resolve().parent.parent


def reply(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    del messages, info
    return ModelResponse(parts=[TextPart("x")])


class ProviderLikeModel(FunctionModel):
    @property
    def system(self) -> str:
        return "provider-like"


def test_scripted_models_never_calibrate_but_provider_usage_does() -> None:
    assert not reports_real_usage(FunctionModel(reply))
    assert not reports_real_usage(TestModel())
    assert reports_real_usage(ProviderLikeModel(reply))
    scripted, real = Calibration(), Calibration()
    for calibration, flag in ((scripted, False), (real, True)):
        calibration.begin_request(10_000, appended=True)
        calibration.record_usage(5_000, real_usage=flag)
    assert (scripted.last_reported_input, scripted.chars_per_token) == (None, 4.0)
    assert (real.last_reported_input, real.chars_per_token) == (5_000, 3.0)
    assert scripted.estimate(12_000, real_usage=False, appended=True) == (3_000, "fixed_ratio")
    assert real.estimate(12_000, real_usage=True, appended=True) == (5_800, "anchored")
    assert real.estimate(8_000, real_usage=True, appended=True) == (2_667, "calibrated")  # shrank: overall ratio
    # A small addition (under 200 tokens) does not move the new-text ratio.
    real.begin_request(10_400, appended=True)
    real.record_usage(5_150, real_usage=True)
    assert real.new_text_chars_per_token == 2.5


def test_the_file_limit_uses_the_indented_serialisation() -> None:
    entries = [Entry("a", "note", "é")]
    text = serialize(entries)
    assert (
        text
        == '{\n "format": "clm-context/v1",\n "entries": [\n  {\n   "id": "a",\n   "role": "note",\n   "body": "é"\n  }\n ]\n}\n'
    )
    raw = text.encode("utf-8")
    assert parse_candidate(raw, ContextLimits(max_file_bytes=len(raw))) == tuple(entries)
    with pytest.raises(ValueError, match=f"file is {len(raw)} bytes; limit {len(raw) - 1}"):
        parse_candidate(raw, ContextLimits(max_file_bytes=len(raw) - 1))


def test_protection_is_by_exact_pattern_including_collision_suffixes() -> None:
    store = ContextStore()
    store.append([Entry("t3.answer", "note", "a model note")], "model_edit", 1)
    store.append([Entry("t3.answer", "assistant", "the real answer")], "runtime", 2)
    assert [e.id for e in store.entries] == ["t3.answer", "t3.answer~2"]
    assert is_protected("t3.answer") and not is_protected("t3.answer~2")


def test_non_text_prompt_content_is_ignored() -> None:
    from pydantic_ai import BinaryContent

    part = UserPromptPart(["look at this", BinaryContent(data=b"\x00", media_type="image/png"), " please"])
    assert part_text(part) == "look at this please"


def test_reported_overshoot_ends_the_session_only_when_configured() -> None:
    assert reported_overshoot(32_000, 32_000, "agent") is None
    assert reported_overshoot(32_001, 32_000, "agent") == {
        "type": "context_limit",
        "source": "agent",
        "reported_input_tokens": 32_001,
        "budget_tokens": 32_000,
    }
    responses = iter(
        [ModelResponse(parts=[ToolCallPart("edit_context", {"ops": []}, "e1")]), ModelResponse(parts=[TextPart("x")])]
    )

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del messages, info
        response = next(responses)
        response.usage = RequestUsage(input_tokens=40_000, output_tokens=1)
        return response

    def run(end_over_budget: bool) -> list[str]:
        nonlocal responses
        responses = iter(
            [
                ModelResponse(parts=[ToolCallPart("edit_context", {"ops": []}, "e1")]),
                ModelResponse(parts=[TextPart("x")]),
            ]
        )
        records: list[dict[str, Any]] = []
        agent = Agent(
            ProviderLikeModel(respond),
            capabilities=[clm_context(end_over_budget=end_over_budget, on_record=records.append)],
        )
        try:
            asyncio.run(agent.run("go", conversation_id="c"))
        except BudgetExceededError:
            return [r["type"] for r in records] + ["BudgetExceededError"]
        except Exception as error:
            return [r["type"] for r in records] + [type(error).__name__]
        return [r["type"] for r in records] + ["ok"]

    assert run(True)[-2:] == ["context_limit", "BudgetExceededError"]
    assert run(False)[-1] == "ContextLimitError"  # the next estimate, anchored on 40,000, is over the budget


def test_the_status_call_cap_defaults_to_144_whatever_max_steps() -> None:
    assert ClmContext(max_steps=10).call_cap == 144
    steps, context = clm_context(max_steps=10).capabilities  # the factory passes the step limit's cap
    assert context.call_cap == steps.call_cap == 44  # type: ignore[attr-defined]


def test_the_example_runs_offline() -> None:
    out = subprocess.run(
        [sys.executable, str(ROOT / "examples" / "basic_agent.py")], capture_output=True, text=True, check=True
    )
    assert "agent: The last deploy was at 08:55 UTC." in out.stdout
    assert '{"id": "t1.answer", "role": "assistant"' in out.stdout and '"protected": true' in out.stdout
    assert "record: lite_edit" in out.stdout
