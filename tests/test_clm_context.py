"""Readable checks of ClmContext's behaviour. The golden tests are the exact contract; these say what it means."""

import asyncio
import dataclasses
import json
import math
from typing import Any

import pytest
from pydantic_ai import Agent, UsageLimits
from pydantic_ai.capabilities import AbstractCapability, CombinedCapability
from pydantic_ai.exceptions import AgentRunError, UserError
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage

from guarded_clm import (
    CALL_CAP,
    BudgetExceededError,
    ClmContext,
    ClmStepLimit,
    ContextLimitError,
    ContextLimits,
    StepLimitError,
    clm_context,
)
from guarded_clm.capability import cap, is_protected, receipt, render_marked, short_args
from guarded_clm.context import ContextStore, ContextValidationError, EditOutcome, Entry, parse_candidate, serialize
from guarded_clm.estimate import Calibration, ConversationStore, enforced_limit, request_chars
from guarded_clm.instructions import INSTRUCTIONS, instructions


def note() -> str:
    """Read the note."""
    return "the note says: hello"


class Script:
    def __init__(self, responses: list[ModelResponse]) -> None:
        self.responses, self.seen = responses, []

    def model(self) -> FunctionModel:
        def respond(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
            self.seen.append(messages)
            return self.responses[len(self.seen) - 1]

        return FunctionModel(respond)


def call(tool: str, args: dict[str, Any], call_id: str) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(tool, args, call_id)])


def text(content: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content)])


def user_text(messages: list[ModelMessage]) -> str:
    [request] = messages
    assert isinstance(request, ModelRequest)
    return next(str(p.content) for p in request.parts if isinstance(p, UserPromptPart))


def entries(rendered: str) -> list[dict[str, Any]]:
    body = rendered.split("<working_context>\n", 1)[1].split("\n</working_context>", 1)[0]
    return [json.loads(line) for line in body.splitlines() if line.strip()]


def run(script: Script, *prompts: str, **settings: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    agent = Agent(
        script.model(),
        system_prompt="SYSTEM",
        tools=[note],
        capabilities=[clm_context(on_record=records.append, **settings)],
    )
    history = None
    for prompt in prompts:
        result = asyncio.run(agent.run(prompt, message_history=history, conversation_id="c"))
        history = result.all_messages()
    return records


# ---------------------------------------------------------------------- the public API


def test_clm_context_is_the_step_limit_then_the_context_with_the_tested_defaults() -> None:
    combined = clm_context()
    assert isinstance(combined, CombinedCapability)
    steps, context = combined.capabilities
    assert isinstance(steps, ClmStepLimit) and isinstance(context, ClmContext)
    assert (steps.max_steps, steps.call_cap, steps.finalize) == (60, 144, True)
    assert (context.budget_tokens, context.limit, context.max_output_chars, context.end_over_budget) == (
        32_000,
        29_952,
        8_000,
        False,
    )
    assert (context.max_steps, context.call_cap) == (60, 144) and CALL_CAP == 144
    assert context.limits == ContextLimits()
    assert enforced_limit(32_000) == 29_952


class Named(AbstractCapability[Any]):
    """Another capability with ClmContext's default id, such as a separate tracing capability."""

    def __init__(self) -> None:
        self.id = "clm_context"


def test_the_capability_id_is_configurable_and_kept_per_run() -> None:
    def agent(script: Script, capability: CombinedCapability[Any]) -> Agent[None, str]:
        return Agent(script.model(), system_prompt="SYSTEM", tools=[note], capabilities=[capability, Named()])

    with pytest.raises(UserError, match="is used by multiple capabilities"):
        asyncio.run(agent(Script([text("x")]), clm_context()).run("go", conversation_id="c"))

    ops = [{"op": "add_note", "id": "n1", "body": "kept"}, {"op": "delete", "ids": ["s1.obs"]}]
    script = Script([call("note", {}, "c1"), call("edit_context", {"ops": ops}, "e1"), text("one"), text("two")])
    records: list[dict[str, Any]] = []
    combined = clm_context(id="clm_context_a", on_record=records.append)
    assert [c.id for c in combined.capabilities] == ["clm_step_limit", "clm_context_a"]
    history = None
    for prompt in ("first", "second"):
        result = asyncio.run(agent(script, combined).run(prompt, message_history=history, conversation_id="c"))
        history = result.all_messages()
    # The edit reached this run's context, so `resolve()` found the per-run copy by its id.
    assert [r["status"] for r in records if r["type"] == "lite_edit"] == ["accepted"]
    ids = [e["id"] for e in entries(user_text(script.seen[3]))]
    assert ids == ["s1.act", "n1", "s2.act", "s2.obs", "s2.rcpt", "t1.answer"]


def test_the_errors_end_the_run_as_agent_run_errors() -> None:
    assert issubclass(ContextLimitError, AgentRunError) and issubclass(StepLimitError, AgentRunError)
    assert issubclass(BudgetExceededError, ContextLimitError)


def test_the_instructions_are_the_tested_text() -> None:
    text = instructions(ContextLimits())
    assert text == INSTRUCTIONS.format(max_entries=200, max_body=24000)
    assert "You may call several tools in one step; they all run." in text
    assert (
        "- Entries marked protected (earlier requests and your earlier answers) can't be edited or deleted; tidy "
        "everything else as needed." in text
    )
    assert "Limits: 200 entries, 24000 chars per body." in text


# ---------------------------------------------------------------------- the request


def test_every_request_is_rebuilt_as_task_working_context_and_status() -> None:
    script = Script([call("note", {}, "c1"), text("done")])
    run(script, "the task")
    second = script.seen[1]
    assert len(second) == 1  # one request: the system prompt and one user message
    rendered = user_text(second)
    assert rendered.startswith("<task>\nthe task\n</task>\n\n<working_context>\n")
    assert [e["id"] for e in entries(rendered)] == ["s1.act", "s1.obs"]
    status = rendered.split("<runtime_status>\n", 1)[1]
    assert status.startswith("step 2; task steps used this turn 1 of 60 (edit_context steps are free); model calls")


def test_later_turns_keep_the_first_request_in_the_task_and_protect_earlier_turns() -> None:
    script = Script([text("one"), text("two"), text("three")])
    run(script, "first", "second", "third")
    rendered = user_text(script.seen[2])
    assert rendered.startswith("<task>\nfirst\n\nCurrent request:\nthird\n</task>")
    marked = {e["id"]: e.get("protected") for e in entries(rendered)}
    assert marked == {"t1.answer": True, "t2.answer": True, "t2.request": True}


# ---------------------------------------------------------------------- edits


def test_an_edit_touching_a_protected_entry_is_refused_whole() -> None:
    ops = [{"op": "delete", "ids": ["s1.obs", "t1.answer"]}]
    script = Script([call("note", {}, "c1"), text("one"), call("edit_context", {"ops": ops}, "e1"), text("two")])
    records = run(script, "first", "second")
    after = entries(user_text(script.seen[3]))
    assert "s1.obs" in [e["id"] for e in after]  # nothing was deleted
    rcpt = next(e for e in after if e["id"] == "s2.rcpt")["body"]
    assert rcpt == "context.json rejected: protected ids ['t1.answer']; the previous context is kept."
    # A tool runs before its step is folded into the context, so its records carry the previous step's number.
    assert {"variant": "clm_lite_v3_3", "type": "lite_protected_refused", "step": 1, "ids": ["t1.answer"]} in records


def test_edit_steps_are_free_task_steps() -> None:
    ops = [{"op": "add_note", "id": "n1", "body": "kept"}]
    script = Script([call("edit_context", {"ops": ops}, "e1"), call("note", {}, "c1"), text("done")])
    run(script, "go")
    status = user_text(script.seen[2]).split("<runtime_status>\n", 1)[1]
    assert status.startswith("step 3; task steps used this turn 1 of 60")


def test_protected_ids_and_marking() -> None:
    assert is_protected("t1.request") and is_protected("t12.answer")
    assert not is_protected("t1.answer~2") and not is_protected("s1.obs") and not is_protected("t.answer")
    line = render_marked([Entry("t1.answer", "assistant", "<b>")])
    assert line == '{"id": "t1.answer", "role": "assistant", "body": "\\u003cb>", "protected": true}'


# ---------------------------------------------------------------------- limits and endings


def test_the_step_limit_ends_the_run() -> None:
    script = Script([call("note", {}, f"c{i}") for i in range(10)])
    with pytest.raises(StepLimitError, match="task-step limit of 3"):
        run(script, "go", max_steps=3, call_cap=20)
    notice = user_text(script.seen[1]).split("<runtime_status>\n", 1)[1]
    assert "[context manager] 2 step(s) left: finish now and give your final answer." in notice


def test_a_second_request_in_a_row_over_the_limit_ends_the_run() -> None:
    script = Script([call("note", {}, f"c{i}") for i in range(10)])
    with pytest.raises(ContextLimitError):
        run(script, "x" * 9_000, budget_tokens=3_000)  # about 3,000 tokens of task alone, over the 952 limit


# ---------------------------------------------------------------------- the parts


def test_the_store_suffixes_colliding_runtime_ids() -> None:
    store = ContextStore()
    store.append([Entry("a", "note", "1")], "runtime", 0)
    store.append([Entry("a", "note", "2"), Entry("a", "note", "3")], "runtime", 0)
    assert [e.id for e in store.entries] == ["a", "a~2", "a~3"]


def test_validation_messages() -> None:
    limits = ContextLimits(max_entries=1, max_body_chars=5, max_total_chars=5)
    with pytest.raises(ContextValidationError, match="2 entries; limit 1"):
        parse_candidate(serialize([Entry("a", "note", ""), Entry("b", "note", "")]).encode(), limits)
    with pytest.raises(ContextValidationError, match="invalid id"):
        parse_candidate(serialize([Entry("-a", "note", "")]).encode(), limits)
    with pytest.raises(ContextValidationError, match="role 'user' not allowed"):
        parse_candidate(serialize([Entry("a", "user", "")]).encode(), limits)
    with pytest.raises(ContextValidationError, match="body 6 chars; limit 5"):
        parse_candidate(serialize([Entry("a", "note", "123456")]).encode(), limits)


def test_receipts_and_the_cap() -> None:
    store = ContextStore()
    before = store.current
    after = store.commit([Entry("n1", "note", "x")], "model_edit", 1)
    assert receipt(EditOutcome("accepted", before=before, after=after)) == (
        "context.json accepted as revision 1: 0 removed [], 1 added ['n1'], 0 rewritten []."
    )
    assert receipt(EditOutcome("unchanged_write", before=before)) == (
        "context.json unchanged write; the previous context is kept."
    )
    assert cap("abc", 3) == "abc"
    capped = cap("x" * 100, 60)
    assert "[... 40 characters omitted ...]" in capped and len(capped) == 60


def test_calibration_keeps_the_lowest_new_text_ratio() -> None:
    calibration = Calibration()
    assert calibration.estimate(1_000, real_usage=True, appended=True) == (400, "calibrated")  # 2.5 chars a token
    calibration.begin_request(1_000, appended=True)
    calibration.record_usage(400, real_usage=True)
    calibration.begin_request(5_000, appended=True)
    calibration.record_usage(2_400, real_usage=True)  # 4,000 new characters at 2,000 tokens: 2.0
    assert calibration.new_text_chars_per_token == 2.0
    assert calibration.estimate(7_000, real_usage=True, appended=True) == (3_400, "anchored")
    assert calibration.estimate(7_000, real_usage=False, appended=True) == (1_750, "fixed_ratio")


def test_held_back_arguments_are_shortened_past_80_characters() -> None:
    exact = ToolCallPart("search", {"q": "x" * 72}, "a")  # {"q":"..."} is 80 characters
    assert short_args(exact) == exact.args_as_json_str() and len(short_args(exact)) == 80
    longer = ToolCallPart("search", {"q": "x" * 73}, "b")
    assert len(short_args(longer)) == 80 and short_args(longer).endswith("…")


def test_conversation_state_keeps_the_512_most_recent_conversations() -> None:
    store: ConversationStore[list[str]] = ConversationStore(list)
    first = store.get("c0")
    for i in range(1, 513):
        store.get(f"c{i}")
    assert store.get("c0") is not first  # evicted, so a fresh state
    assert store.get("c512") is store.get("c512")


# ---------------------------------------------------------------------- strict ratio learning (decisions P6)


def collapse(calibration: Calibration, *, strict: bool) -> None:
    """Synthetic real-usage calls: an appended step at 2.0 characters a token, then a step whose edit removed about
    as much as it added: the request grew by 40 characters while the reported input rose by 250 tokens."""
    calibration.begin_request(10_000, appended=True)
    calibration.record_usage(4_000, real_usage=True, strict=strict, extends=False)  # the first call
    calibration.begin_request(18_000, appended=True)
    calibration.record_usage(8_000, real_usage=True, strict=strict, extends=True)  # 8,000 characters at 4,000 tokens
    calibration.begin_request(18_040, appended=True)  # no smaller than the last, so "appended" by size
    calibration.record_usage(8_250, real_usage=True, strict=strict, extends=False)


def test_without_strict_learning_a_non_extending_step_collapses_the_new_text_ratio() -> None:
    calibration = Calibration()
    collapse(calibration, strict=False)
    assert calibration.new_text_chars_per_token == 0.16  # 40 characters / 250 tokens
    tokens, method = calibration.estimate(48_040, real_usage=True, appended=True)  # 30,000 characters added
    assert method == "anchored" and tokens == 195_750 > enforced_limit(32_000)


def test_with_strict_learning_a_non_extending_step_leaves_the_new_text_ratio() -> None:
    calibration = Calibration()
    collapse(calibration, strict=True)
    assert calibration.new_text_chars_per_token == 2.0  # learnt from the extending step only
    assert calibration.estimate(48_040, real_usage=True, appended=True) == (23_250, "anchored")
    assert enforced_limit(32_000) >= 23_250


def test_strict_learning_ignores_an_implausible_observation_even_from_an_extending_step() -> None:
    calibration = Calibration()
    calibration.begin_request(10_000, appended=True)
    calibration.record_usage(4_000, real_usage=True, strict=True, extends=False)
    calibration.begin_request(10_100, appended=True)
    calibration.record_usage(4_300, real_usage=True, strict=True, extends=True)  # 100 / 300: 0.33
    assert calibration.new_text_chars_per_token == 2.5
    calibration.begin_request(10_500, appended=True)
    calibration.record_usage(4_500, real_usage=True, strict=True, extends=True)  # 400 / 200: 2.0
    assert calibration.new_text_chars_per_token == 2.0


def test_strict_learning_matches_the_default_on_extending_steps() -> None:
    sizes = [(10_000, 4_000), (14_000, 5_800), (19_000, 8_300), (19_150, 8_400), (25_000, 11_200)]
    default, strict = Calibration(), Calibration()
    for calibration, is_strict in ((default, False), (strict, True)):
        for chars, reported in sizes:
            calibration.begin_request(chars, appended=True)
            calibration.record_usage(reported, real_usage=True, strict=is_strict, extends=True)
    assert default == strict and default.new_text_chars_per_token < 2.5


class ProviderUsageModel(FunctionModel):
    @property
    def system(self) -> str:
        return "provider"  # not a scripted system, so its reported usage calibrates the shared estimator


def read_page(n: int, size: int) -> str:
    """Read a page of the log."""
    return f"page {n}: " + " ".join(f"line{i:05d}" for i in range(size // 10))[:size]


class UsageScript(Script):
    """Plays responses and reports provider-like input tokens: the request's characters at 2.4 characters a token,
    or, on the calls in `rises`, the last reported input plus ten tokens per character the request grew by (at
    least 250), as when an edit removes about as much as a step adds."""

    def __init__(self, responses: list[ModelResponse], rises: frozenset[int] = frozenset()) -> None:
        super().__init__(responses)
        self.rises, self.reported, self.chars = rises, [], []

    def model(self) -> FunctionModel:
        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            self.seen.append(messages)
            index = len(self.seen) - 1
            chars = request_chars(messages)
            if index in self.rises:
                tokens = self.reported[-1] + max(250, 10 * (chars - self.chars[-1]))
            else:
                tokens = math.ceil(chars / 2.4)
            self.chars.append(chars)
            self.reported.append(tokens)
            response = self.responses[index]
            return dataclasses.replace(response, usage=RequestUsage(input_tokens=tokens, output_tokens=10))

        return ProviderUsageModel(respond)


def run_with_usage(script: UsageScript, *, strict: bool) -> tuple[ClmContext, list[dict[str, Any]], Any]:
    records: list[dict[str, Any]] = []
    context = ClmContext(on_record=records.append, strict_ratio_learning=strict)
    agent = Agent(script.model(), system_prompt="SYSTEM", tools=[read_page], capabilities=[ClmStepLimit(), context])
    try:
        outcome: Any = asyncio.run(
            agent.run("Investigate.", conversation_id="c", usage_limits=UsageLimits(request_limit=CALL_CAP))
        )
    except AgentRunError as error:
        outcome = error
    return context, records, outcome


def collapse_script() -> UsageScript:
    return UsageScript(
        [
            call("read_page", {"n": 1, "size": 3_000}, "p1"),
            ModelResponse(
                parts=[
                    ToolCallPart("edit_context", {"ops": [{"op": "delete", "ids": ["s1.obs"]}]}, "e1"),
                    ToolCallPart("read_page", {"n": 2, "size": 2_640}, "p2"),  # the request grows by a few characters
                ]
            ),
            call("read_page", {"n": 3, "size": 6_000}, "p3"),
            text("done"),
        ],
        rises=frozenset({2}),
    )


def test_a_non_extending_step_ends_the_run_without_strict_learning() -> None:
    script = collapse_script()
    context, records, outcome = run_with_usage(script, strict=False)
    assert isinstance(outcome, ContextLimitError)
    [ending] = [r for r in records if r["type"] == "context_limit"]
    assert ending["reason"] == "over the budget itself" and ending["estimated_tokens"] > 32_000
    assert max(script.reported) < 10_000  # no reported input came near the budget
    assert context.estimators.get("c").calibration.new_text_chars_per_token < 0.5


def test_strict_learning_keeps_the_estimate_in_range_after_a_non_extending_step() -> None:
    script = collapse_script()
    context, records, outcome = run_with_usage(script, strict=True)
    assert outcome.output == "done"
    assert not [r for r in records if r["type"] in ("context_limit", "lite_over_limit")]
    assert any(r["type"] == "lite_edit" and r["status"] == "accepted" for r in records)  # s1.obs was deleted
    assert context.estimators.get("c").calibration.new_text_chars_per_token > 2.0


def test_strict_learning_is_identical_on_a_purely_appending_run() -> None:
    def appending() -> UsageScript:
        return UsageScript(
            [
                call("read_page", {"n": 1, "size": 3_000}, "p1"),
                call("read_page", {"n": 2, "size": 4_000}, "p2"),
                call("read_page", {"n": 3, "size": 2_000}, "p3"),
                text("done"),
            ]
        )

    default_script, strict_script = appending(), appending()
    default, default_records, default_outcome = run_with_usage(default_script, strict=False)
    strict, strict_records, strict_outcome = run_with_usage(strict_script, strict=True)
    assert default_outcome.output == strict_outcome.output == "done"
    assert [user_text(m) for m in default_script.seen] == [user_text(m) for m in strict_script.seen]
    assert default_records == strict_records
    learnt = default.estimators.get("c").calibration
    assert learnt == strict.estimators.get("c").calibration and learnt.new_text_chars_per_token < 2.5
