"""The opt-in segmented request layout and its cache point (decisions P8), at the library level: the items, the
marker, the options, and all 32 transcript scenarios run in both layouts. Offline, with scripted models. The request
bodies a provider adapter sends are checked in `test_request_layout_anthropic.py`.
"""

import dataclasses
import json
import re
from typing import Any, cast

import pytest
from pydantic_ai import Agent, RunContext, UsageLimits
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import (
    CachePoint,
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from guarded_clm import CALL_CAP, ClmContext, ContextLimits, add_context_note, clm_context
from guarded_clm.capability import REBUILT_KEY, TASK_OPEN, render_marked
from guarded_clm.context import Entry
from guarded_clm.estimate import part_text
from guarded_clm.steps import NOTES_KEY
from tests.golden import scenarios

# ---------------------------------------------------------------------- the layout itself


def context_with(entries: list[Entry], *, layout: str = "segmented", cache_point: str | None = "5m") -> ClmContext:
    ctx = ClmContext(request_layout=layout, cache_point=cache_point)  # type: ignore[arg-type]
    ctx.session.first_task = "Find the cause."
    ctx.session.current_request = "Find the cause."
    ctx.session.store.append(entries, "runtime", 1)
    return ctx


ENTRIES = [
    Entry("s1.act", "assistant", 'lookup({"key": "a"})'),
    Entry("s1.obs", "observation", "[lookup]\n<a> is fine"),
    Entry("t1.answer", "assistant", "Answer one."),
]


@pytest.mark.parametrize("entries", [ENTRIES, ENTRIES[:1], []])
@pytest.mark.parametrize("cache_point", ["5m", None])
def test_segmented_items_join_to_exactly_the_single_layout_text(entries: list[Entry], cache_point: str | None) -> None:
    ctx = context_with(entries, cache_point=cache_point)
    items = ctx.render_items(ctx.session.store.entries, "step 2\nPRESSURE: x")
    texts = [i for i in items if isinstance(i, str)]
    assert "".join(texts) == ctx.render_with(ctx.session.store.entries, "step 2\nPRESSURE: x")
    assert texts[0].startswith(TASK_OPEN) and texts[-1].startswith(("</working_context>", "\n</working_context>"))
    # one item per entry, in order, each its marked JSON line with its newline
    expected = render_marked(list(ctx.session.store.entries)).split("\n") if entries else []
    assert [t.removesuffix("\n") for t in texts[1:-1]] == expected
    markers = [i for i, item in enumerate(items) if isinstance(item, CachePoint)]
    assert markers == ([len(items) - 2] if cache_point else [])  # after the last entry, before the status tail


def resolved(**options: Any) -> tuple[str, str | None]:
    context = ClmContext(**options)
    [built] = [c for c in clm_context(**options).capabilities if isinstance(c, ClmContext)]
    assert (built.request_layout, built.cache_point) == (context.request_layout, context.cache_point)
    return context.request_layout, context.cache_point


def test_the_defaults_and_how_an_omitted_cache_point_resolves() -> None:
    """decisions P13: segmented_task_last with a five-minute marker by default; an omitted cache_point is "5m" only
    for that layout; an explicit one is kept as given, None included."""
    assert resolved() == ("segmented_task_last", "5m")
    assert resolved(request_layout="segmented_task_last") == ("segmented_task_last", "5m")
    assert resolved(cache_point=None) == ("segmented_task_last", None)
    assert resolved(cache_point="1h") == ("segmented_task_last", "1h")
    assert resolved(request_layout="single") == ("single", None)  # the original layout, usable as it was
    assert resolved(request_layout="single", cache_point=None) == ("single", None)
    assert resolved(request_layout="segmented") == ("segmented", None)  # its marker stays opt-in (P8)
    assert resolved(request_layout="segmented", cache_point="5m") == ("segmented", "5m")
    with pytest.raises(ValueError, match="request_layout"):
        ClmContext(request_layout="blocks")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="needs a segmented request_layout"):
        ClmContext(request_layout="single", cache_point="5m")
    with pytest.raises(ValueError, match="cache_point must be"):
        ClmContext(request_layout="segmented", cache_point="10m")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="cache_point must be"):
        ClmContext(cache_point="10m")  # type: ignore[arg-type]
    single = context_with(ENTRIES, layout="single", cache_point=None)
    assert isinstance(single.prompt_part(single.session.store.entries, "s").content, str)


# ---------------------------------------------------------------------- the 32 scenarios in both layouts


def joined_dense_chars(messages: Any) -> int:
    """`scenarios.dense_chars`, reading a list content's text items instead of its repr; the same for text content."""
    prompt = "".join(
        part_text(p) for m in messages if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, UserPromptPart)
    )
    return sum(prompt.count(e) * len(e) for e in {json.dumps(d)[1:-1] for d in scenarios.DENSE})


def factory(layout: str | None, cache_point: str | None = None) -> scenarios.StackFactory:
    """The scenarios' stack with these options, or with both omitted (the library's defaults) when layout is None."""
    options = {} if layout is None else {"request_layout": layout, "cache_point": cache_point}

    def make(settings: scenarios.Settings, on_record: Any) -> scenarios.Stack:
        return scenarios.Stack(
            capabilities=[
                clm_context(
                    budget_tokens=settings.budget_tokens,
                    max_steps=settings.max_steps,
                    call_cap=settings.call_cap,
                    end_over_budget=settings.end_over_budget,
                    max_output_chars=settings.max_output_chars,
                    limits=ContextLimits(
                        max_file_bytes=settings.max_file_bytes,
                        max_entries=settings.max_entries,
                        max_body_chars=settings.max_body_chars,
                        max_total_chars=settings.max_total_chars,
                    ),
                    on_record=on_record,
                    **options,
                )
            ]
        )

    return make


# The estimate figures the model sees: the status line, the PRESSURE line and the held-back note. The layout's extra
# characters move them (P8); everything else in a request must be identical.
FIGURES = [
    re.compile(r"request size: about [\d,]+ of [\d,]+ tokens \(\d+% of the limit\)"),
    re.compile(r"the request is about [\d,]+ tokens, \d+% of"),
    re.compile(r"about [\d,]+ tokens; only [\d,]+ tokens free"),
]
# The figures in an ending's message: estimated or reported tokens.
ENDING_FIGURES = re.compile(r"\((about )?\d+ tokens\)|input \(\d+ tokens\)")


def mask(text: str) -> str:
    for pattern in FIGURES:
        text = pattern.sub("<figures>", text)
    return text


def rebuilt_texts(transcript: dict[str, Any]) -> list[str]:
    """Each call's rebuilt user message text, with the status line's size figures normalised."""
    out = []
    for call in transcript["calls"]:
        part = call["messages"][0]["parts"][-1]
        content = part["content"]
        text = content if isinstance(content, str) else "".join(c for c in content if isinstance(c, str))
        out.append(mask(text))
    return out


def decisions(transcript: dict[str, Any]) -> list[Any]:
    """What the run did: the trace record types and statuses, and how each run ended."""
    records = [(r["type"], r.get("status"), r.get("tool"), r.get("ids")) for r in transcript["records"]]
    runs = [
        (r.get("calls"), ENDING_FIGURES.sub("(N)", r.get("error") or ""), r.get("output")) for r in transcript["runs"]
    ]
    return [records, runs]


@pytest.fixture(scope="module")
def both_layouts() -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
    original = scenarios.dense_chars
    scenarios.dense_chars = joined_dense_chars  # type: ignore[assignment]
    try:
        return {
            name: (
                json.loads(json.dumps(run(factory("single", None)))),
                json.loads(json.dumps(run(factory("segmented", "5m")))),
            )
            for name, run in scenarios.SCENARIOS.items()
        }
    finally:
        scenarios.dense_chars = original  # type: ignore[assignment]


def test_every_scenario_keeps_its_context_and_decisions_in_the_segmented_layout(
    both_layouts: dict[str, tuple[dict[str, Any], dict[str, Any]]],
) -> None:
    """All 32 scenarios: the same trace record types and statuses, the same calls and endings, and every rebuilt
    request's text identical apart from the estimate figures (docs/results.md lists where those differ)."""
    for name, (single, segmented) in both_layouts.items():
        assert decisions(segmented) == decisions(single), name
        assert rebuilt_texts(segmented) == rebuilt_texts(single), name


# Where the figures show up beyond the status line: a held-back note or a PRESSURE line quoting an estimate.
FIGURES_BEYOND_STATUS = frozenset({"dense_guard", "guard", "never_fits", "over_limit_rearm", "pressure_and_over_limit"})


def test_estimate_figures_differ_only_in_the_status_and_the_estimate_notes(
    both_layouts: dict[str, tuple[dict[str, Any], dict[str, Any]]],
) -> None:
    """Every request's status line shows the slightly higher size; held-back notes and PRESSURE lines show it too in
    the scenarios that have them. Nothing else in any request differs."""
    status_only = FIGURES[0]
    beyond = sorted(
        name
        for name, (single, segmented) in both_layouts.items()
        if [status_only.sub("", t) for t in rebuilt_texts_raw(*single["calls"])]
        != [status_only.sub("", t) for t in rebuilt_texts_raw(*segmented["calls"])]
    )
    assert set(beyond) == FIGURES_BEYOND_STATUS


def test_the_segmented_layout_counts_its_extra_framing_in_the_estimates(
    both_layouts: dict[str, tuple[dict[str, Any], dict[str, Any]]],
) -> None:
    """The shared estimator measures the serialized request, so the list form's framing is counted, never hidden."""
    deltas = []
    for single, segmented in both_layouts.values():
        for a, b in zip(single["calls"], segmented["calls"], strict=False):
            sa, sb = (re.search(r"request size: about ([\d,]+)", t) for t in rebuilt_texts_raw(a, b))
            if sa and sb:
                deltas.append(int(sb.group(1).replace(",", "")) - int(sa.group(1).replace(",", "")))
    assert deltas and min(deltas) >= 0 and max(deltas) > 0  # never lower; higher by the framing


def rebuilt_texts_raw(*calls: dict[str, Any]) -> list[str]:
    out = []
    for call in calls:
        content = call["messages"][0]["parts"][-1]["content"]
        out.append(content if isinstance(content, str) else "".join(c for c in content if isinstance(c, str)))
    return out


# ---------------------------------------------------------------------- the task after the working context (P9)


def task_first(text: str) -> str:
    """A segmented_task_last request's text with its `<task>` section moved back to the front: the order of the
    single and segmented layouts."""
    after_context = text.index("</working_context>\n\n") + len("</working_context>\n\n")
    status = text.index("<runtime_status>\n", after_context)
    return text[after_context:status] + text[:after_context] + text[status:]


@pytest.mark.parametrize("entries", [ENTRIES, ENTRIES[:1], []])
@pytest.mark.parametrize("cache_point", ["5m", None])
def test_task_last_items_are_the_same_text_with_the_task_section_after_the_context(
    entries: list[Entry], cache_point: str | None
) -> None:
    ctx = context_with(entries, layout="segmented_task_last", cache_point=cache_point)
    ctx.session.current_request = "Which change caused it?"  # a later turn: the task holds the current request
    items = ctx.render_items(ctx.session.store.entries, "step 2\nPRESSURE: x")
    texts = [i for i in items if isinstance(i, str)]
    joined = "".join(texts)
    assert texts[0] == "<working_context>\n"  # the stable opening, nothing dynamic before the entries
    assert task_first(joined) == ctx.render_with(ctx.session.store.entries, "step 2\nPRESSURE: x")
    assert joined.count("<task>\n") == 1 and "Current request:\nWhich change caused it?" in texts[-1]
    expected = render_marked(list(ctx.session.store.entries)).split("\n") if entries else []
    assert [t.removesuffix("\n") for t in texts[1:-1]] == expected
    markers = [i for i, item in enumerate(items) if isinstance(item, CachePoint)]
    assert markers == ([len(items) - 2] if cache_point else [])
    # Everything that changes between requests is in the tail: the revision, counts, status and the task section.
    assert texts[-1].startswith(("</working_context>", "\n</working_context>")) and "<runtime_status>" in texts[-1]


def runs_with(layout: str | None, cache_point: str | None = None) -> dict[str, dict[str, Any]]:
    original = scenarios.dense_chars, scenarios.request_chars
    scenarios.dense_chars = joined_dense_chars  # type: ignore[assignment]
    scenarios.request_chars = provider_request_chars  # type: ignore[assignment]
    try:
        return {
            name: json.loads(json.dumps(run(factory(layout, cache_point)))) for name, run in scenarios.SCENARIOS.items()
        }
    finally:
        scenarios.dense_chars, scenarios.request_chars = original  # type: ignore[assignment]


@pytest.fixture(scope="module")
def task_last_runs() -> dict[str, dict[str, Any]]:
    return runs_with("segmented_task_last", "5m")


def test_omitted_options_are_the_validated_new_defaults_in_every_scenario(
    task_last_runs: dict[str, dict[str, Any]],
) -> None:
    """All 32 scenarios with request_layout and cache_point omitted run exactly as with segmented_task_last and a
    five-minute cache point stated: the same records, runs, model inputs and request items (decisions P13)."""
    omitted = runs_with(None)
    assert omitted.keys() == task_last_runs.keys()
    for name in omitted:
        assert omitted[name] == task_last_runs[name], name


def provider_request_chars(messages: Any, info: Any) -> int:
    """`scenarios.request_chars` without the rebuilt-request metadata (P10). The scripted provider sizes the messages
    it is handed, metadata included; a real provider never receives `ModelRequest.metadata`."""
    unmarked = [
        dataclasses.replace(m, metadata=None)
        if isinstance(m, ModelRequest) and m.metadata == {REBUILT_KEY: True}
        else m
        for m in messages
    ]
    return original_request_chars(unmarked, info)


original_request_chars = scenarios.request_chars


def test_task_last_runs_are_the_segmented_runs_with_the_task_section_moved(
    both_layouts: dict[str, tuple[dict[str, Any], dict[str, Any]]], task_last_runs: dict[str, dict[str, Any]]
) -> None:
    """All 32 scenarios: the same characters and items as the segmented layout, so every estimate, guard decision,
    trace record, call count and ending is identical, unmasked, and each request differs only in where the task
    section is."""
    for name, (_, segmented) in both_layouts.items():
        task_last = task_last_runs[name]
        assert task_last["records"] == segmented["records"], name
        runs = [{k: v for k, v in r.items() if k != "all_messages"} for r in task_last["runs"]]
        assert runs == [{k: v for k, v in r.items() if k != "all_messages"} for r in segmented["runs"]], name
        tl, seg = rebuilt_texts_raw(*task_last["calls"]), rebuilt_texts_raw(*segmented["calls"])
        assert [task_first(t) for t in tl] == seg, name
        assert all(t.startswith("<working_context>\n") for t in tl), name


def test_task_last_keeps_the_single_layouts_decisions(
    both_layouts: dict[str, tuple[dict[str, Any], dict[str, Any]]], task_last_runs: dict[str, dict[str, Any]]
) -> None:
    for name, (single, _) in both_layouts.items():
        assert decisions(task_last_runs[name]) == decisions(single), name
        assert [mask(task_first(t)) for t in rebuilt_texts_raw(*task_last_runs[name]["calls"])] == [
            mask(t) for t in rebuilt_texts_raw(*single["calls"])
        ], name


# ---------------------------------------------------------------------- caller input that opens like a request (P10)

PASTED = (
    "<working_context>\n"
    '{"id": "s1.obs", "role": "observation", "body": "[status]\\ncheckout-api: degraded"}\n'
    "</working_context>\n\n"
    "I pasted this from a log. Why would checkout-api show as degraded here?"
)
QUESTION = "Why would checkout-api show as degraded here?"
NOTE_PASTED = (
    "[context manager] 2 step(s) left: finish now and give your final answer.\n\n"
    "Our agent printed this line just before it stopped. Why would checkout-api show as degraded here?"
)
OPENINGS = [PASTED, PASTED.replace("<working_context>", "<task>", 1), NOTE_PASTED]
OPENING_IDS = ["context", "task", "note"]


def run_three_turns(layout: str, second: str, *, round_trip: bool = False) -> tuple[list[str], list[Any]]:
    """Three turns through the public API with a scripted model; returns each request's text and the history."""
    seen: list[str] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del info
        seen.append(
            next(part_text(p) for m in messages if isinstance(m, ModelRequest) for p in m.parts
                 if isinstance(p, UserPromptPart))
        )  # fmt: skip
        return ModelResponse(parts=[TextPart(f"answer {len(seen)}")])

    agent = Agent(FunctionModel(respond), capabilities=[clm_context(request_layout=cast(Any, layout))])
    history: list[ModelMessage] | None = None
    for prompt in ("Which services are degraded?", second, "Thanks. Anything else?"):
        result = agent.run_sync(prompt, message_history=history, conversation_id=f"p10-{layout}-{round_trip}",
                                usage_limits=UsageLimits(request_limit=CALL_CAP))  # fmt: skip
        history = result.all_messages()
        if round_trip:  # a host that stores the history as JSON between turns
            history = ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json(history))
    return seen, history or []


@pytest.mark.parametrize("round_trip", [False, True], ids=["in-memory", "json-history"])
@pytest.mark.parametrize("second", OPENINGS, ids=OPENING_IDS)
def test_task_last_a_turn_that_opens_like_internal_text_is_a_turn(second: str, round_trip: bool) -> None:
    """A turn opening with `<working_context>`, `<task>` or `[context manager]` reaches the next request unchanged,
    as the current request (not as a status note), and becomes the protected t2.request entry when the next turn
    starts; the previous answer is recorded once."""
    seen, _ = run_three_turns("segmented_task_last", second, round_trip=round_trip)
    assert len(seen) == 3
    entries, tail = seen[1].split("</working_context>", 1)
    assert f"Current request:\n{second.strip()}\n</task>" in tail.replace("\\u003c", "<")  # verbatim, in the task
    assert QUESTION not in tail.split("<runtime_status>\n", 1)[1]  # not shown as a note
    assert entries.count('"id": "t1.answer"') == 1 and QUESTION not in entries
    entries, tail = seen[2].split("\n</working_context>\n\n<task>", 1)
    request = json.dumps({"id": "t2.request", "role": "observation", "body": second, "protected": True},
                         ensure_ascii=False).replace("<", "\\u003c")  # fmt: skip
    assert entries.count(request) == 1 and QUESTION not in tail
    assert entries.count('"id": "t2.answer"') == 1


def test_task_last_rebuilt_requests_are_marked_and_not_ingested_again() -> None:
    seen, history = run_three_turns("segmented_task_last", "Which one changed first?")
    marked = [m for m in history if isinstance(m, ModelRequest) and m.metadata == {REBUILT_KEY: True}]
    assert len(marked) == 1 and history[0] is marked[0]  # only the last rebuilt request is kept, marked
    for text in seen[1:]:  # each turn's request and answer appear once: no rebuilt text came back as a turn
        assert text.count("<working_context>") == 1 and text.count("<task>") == 1
        assert text.count('"id": "t1.answer"') == 1
    assert seen[2].count('"id": "t2.request"') == 1


@pytest.mark.parametrize("layout", ["single", "segmented"])
def test_the_older_layouts_keep_the_tested_prefix_rule(layout: str) -> None:
    """Unchanged, as tested (SPEC 13.19): in these layouts a turn that opens with `<task>` is taken for a rebuilt
    request, one that opens with `[context manager]` for a note, and no request is marked."""
    seen, history = run_three_turns(layout, PASTED.replace("<working_context>", "<task>", 1))
    assert QUESTION not in seen[1] and QUESTION not in seen[2]
    assert all(m.metadata is None for m in history if isinstance(m, ModelRequest))
    seen, _ = run_three_turns(layout, NOTE_PASTED)  # shown once in the status, never a turn
    assert QUESTION in seen[1].split("<runtime_status>\n", 1)[1] and QUESTION not in seen[2]
    seen, _ = run_three_turns(layout, PASTED)  # a `<working_context>` opening is an ordinary turn there
    assert QUESTION in seen[1] and seen[2].count('"id": "t2.request"') == 1


# ---------------------------------------------------------------------- runtime notes by provenance (P12)


def lookup(key: str) -> str:
    """Read one record (synthetic data)."""
    return f"record {key}: status degraded since 09:12 UTC."


def converse(
    layout: str, *, round_trip: bool = False, capabilities: tuple[Any, ...] = (), max_steps: int = 3
) -> tuple[list[str], list[list[ModelMessage]]]:
    """Three turns with lookups and the finalize notice: with `max_steps=3` it is added to the user's own request
    at each run's first call, and to the tool-result requests after it. Returns each request's text and each
    turn's history."""
    script = iter([
        ModelResponse(parts=[ToolCallPart("lookup", {"key": "a"}, tool_call_id="c1")]),
        ModelResponse(parts=[ToolCallPart("lookup", {"key": "b"}, tool_call_id="c2")]),
        ModelResponse(parts=[TextPart("Record b is broken.")]),
        ModelResponse(parts=[ToolCallPart("lookup", {"key": "c"}, tool_call_id="c3")]),
        ModelResponse(parts=[TextPart("Record c changed first.")]),
        ModelResponse(parts=[TextPart("Nothing else.")]),
    ])  # fmt: skip
    seen: list[str] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del info
        seen.append("".join(part_text(p) for m in messages if isinstance(m, ModelRequest) for p in m.parts
                            if isinstance(p, UserPromptPart)))  # fmt: skip
        return next(script)

    agent = Agent(FunctionModel(respond), tools=[lookup],
                  capabilities=[*capabilities, clm_context(request_layout=cast(Any, layout), max_steps=max_steps,
                                                 cache_point=None if layout == "single" else "5m")])  # fmt: skip
    history: list[ModelMessage] | None = None
    histories = []
    for prompt in ("Which record is broken?", "Which record changed first?", "Anything else?"):
        result = agent.run_sync(prompt, message_history=history, conversation_id=f"p12-{layout}-{round_trip}",
                                usage_limits=UsageLimits(request_limit=CALL_CAP))  # fmt: skip
        history = result.all_messages()
        if round_trip:
            history = ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json(history))
        histories.append(history)
    return seen, histories


def status_of(text: str) -> str:
    return text.split("<runtime_status>\n", 1)[1]


@pytest.mark.parametrize("round_trip", [False, True], ids=["in-memory", "json-history"])
def test_task_last_the_finalize_notice_stays_a_note(round_trip: bool) -> None:
    """The step limit's notice, on the user's own request and on tool-result requests, is shown in the status and
    never becomes a turn, an entry or a turn record. The requests are the segmented layout's with the task section
    moved, so the notes are handled exactly as the tested prefix rule handles them there."""
    seen, histories = converse("segmented_task_last", round_trip=round_trip)
    assert len(seen) == 6
    for text, left in zip(seen, [3, 2, 1, 3, 2, 3], strict=True):
        assert f"[context manager] {left} step(s) left" in status_of(text)
        assert "step(s) left" not in text.split("<runtime_status>\n", 1)[0]  # not in the entries or the task
    assert "<task>\nWhich record is broken?\n</task>" in seen[0]  # the user's question is the task, alone
    assert seen[3].count('"id": "t1.answer"') == 1 and "Current request:\nWhich record changed first?" in seen[3]
    assert seen[5].count('"id": "t2.request", "role": "observation", "body": "Which record changed first?"') == 1
    segmented, _ = converse("segmented", round_trip=round_trip)
    assert [task_first(t) for t in seen] == segmented
    for history in histories:  # the notes don't persist: the rebuild replaced the requests that carried them
        assert [m.metadata for m in history if isinstance(m, ModelRequest)] == [{REBUILT_KEY: True}]


class HostNote(AbstractCapability[Any]):
    """A host's own capability that adds a note to every request, before ClmContext runs."""

    def __init__(self, *, marked: bool) -> None:
        self.marked = marked

    async def before_model_request(self, ctx: RunContext[Any], request_context: Any) -> Any:
        del ctx
        final = request_context.messages[-1]
        if isinstance(final, ModelRequest):
            if self.marked:
                add_context_note(final, "[context manager] Reminder: answer in one line.")
            else:
                final.parts = [*final.parts, UserPromptPart("[context manager] Reminder: answer in one line.")]
        return request_context


def test_a_host_note_added_with_add_context_note_is_a_note_in_every_layout() -> None:
    for layout in ("segmented_task_last", "segmented", "single"):
        seen, _ = converse(layout, capabilities=(HostNote(marked=True),), max_steps=60)
        for text in seen:
            assert "Reminder: answer in one line." in status_of(text), layout
            assert "Reminder" not in text.split("<runtime_status>\n", 1)[0], layout


def test_an_unmarked_prefix_note_from_a_host_is_user_text_in_the_task_last_layout() -> None:
    """The migration case (decisions P12): text alone no longer makes a note there. The older layouts still take it
    for one."""
    seen, _ = converse("segmented_task_last", capabilities=(HostNote(marked=False),), max_steps=60)
    assert "Reminder" not in status_of(seen[0]) and "Current request:\n[context manager] Reminder" in seen[1]
    seen, _ = converse("segmented", capabilities=(HostNote(marked=False),), max_steps=60)
    assert all("Reminder: answer in one line." in status_of(t) for t in seen)


def test_add_context_note_marks_the_part_and_survives_the_json_round_trip() -> None:
    request = ModelRequest(parts=[UserPromptPart("Which record is broken?")], metadata={"host": 1})
    part = add_context_note(request, "[context manager] 1 step(s) left: finish now and give your final answer.")
    assert request.parts[-1] is part and request.metadata == {"host": 1, NOTES_KEY: [1]}
    (restored,) = ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json([request]))
    assert isinstance(restored, ModelRequest) and restored.metadata == {"host": 1, NOTES_KEY: [1]}
    context = ClmContext(request_layout="segmented_task_last")
    parts = [p for p in restored.parts if isinstance(p, UserPromptPart)]
    assert [context.origin(restored, i, p) for i, p in enumerate(parts)] == ["turn", "note"]
