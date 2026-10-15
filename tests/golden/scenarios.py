"""Transcript scenarios: scripted conversations that pin ClmContext's behaviour.

Each scenario drives an agent built with a given capability stack through a scripted conversation, and returns a
transcript: every request the model saw (messages, instructions, tool definitions and settings), every trace record,
and how each run ended. `tests/test_golden.py` runs them against this library and requires the transcripts to be
identical to the ones stored in `tests/golden/data/`.

The data is synthetic. This module depends only on Pydantic AI and the standard library, so it can run against any
implementation that exposes the same `clm_context()` factory (`scripts/record_transcripts.py --package`). Never
change a scenario or a stored transcript to make a behaviour change pass (SPEC.md, "Transcript tests").
"""

import asyncio
import contextlib
import dataclasses
import datetime
import json
import math
from collections.abc import Callable, Iterator, Sequence
from typing import Any

from pydantic import BaseModel
from pydantic_ai import Agent, BinaryContent, ImageUrl, ModelRetry, UsageLimits, _utils as pydantic_ai_utils
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage
from pydantic_core import to_jsonable_python

EDIT = "edit_context"
SYSTEM = "You are a careful operations assistant. Use the tools to answer."

# Keys that differ between runs (timestamps, generated ids) or name internal identifiers that are never sent to the
# model (the toolset and capability ids of a tool definition). Everything else must match exactly.
VOLATILE = frozenset({"timestamp", "run_id", "conversation_id", "toolset_id", "capability_id"})

# Every timestamp during a scenario. Timestamps are scrubbed from transcripts, but their serialized length counts
# towards the size estimates, and Pydantic AI writes a timestamp at microsecond 0 without its fraction (7 characters
# shorter). A fixed time with a non-zero microsecond keeps the estimates, and so the transcripts, independent of the
# wall clock (SPEC.md, sections 13 and 15).
FROZEN_TIME = datetime.datetime(2026, 10, 15, 0, 0, 0, 123456, tzinfo=datetime.UTC)


class _FrozenDatetime(datetime.datetime):
    @classmethod
    def now(cls, tz: datetime.tzinfo | None = None) -> datetime.datetime:
        return FROZEN_TIME.astimezone(tz) if tz is not None else FROZEN_TIME.replace(tzinfo=None)


@contextlib.contextmanager
def frozen_clock() -> Iterator[None]:
    """Freeze Pydantic AI's clock at FROZEN_TIME, and restore it afterwards.

    Every message and part timestamp comes from `pydantic_ai._utils.now_utc`. The dataclass default factories and
    the `from ... import now_utc` imports bind that function at import time, so replacing it would miss them. It
    looks up `datetime` in its module on each call, so that name is replaced instead.
    """
    namespace = pydantic_ai_utils.__dict__  # the module's globals, which `now_utc` reads
    original = namespace["datetime"]
    namespace["datetime"] = _FrozenDatetime
    try:
        yield
    finally:
        namespace["datetime"] = original


@dataclasses.dataclass
class Settings:
    """The settings a stack is built with. The defaults are the library's defaults."""

    budget_tokens: int = 32_000
    max_steps: int = 60
    call_cap: int = 144
    max_output_chars: int = 8_000
    end_over_budget: bool = False
    max_entries: int = 200
    max_body_chars: int = 24_000
    max_total_chars: int = 120_000
    max_file_bytes: int = 256_000


@dataclasses.dataclass
class Stack:
    """A capability stack under test. `before_run` is called before each `agent.run`."""

    capabilities: list[AbstractCapability[Any]]
    before_run: Callable[[], None] = lambda: None


StackFactory = Callable[[Settings, Callable[[dict[str, Any]], None]], Stack]


# ---------------------------------------------------------------------- normalisation


def scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items() if k not in VOLATILE}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def dump(value: Any) -> Any:
    return scrub(to_jsonable_python(value))


# ---------------------------------------------------------------------- tools (synthetic data)


def status() -> str:
    """Read the service status."""
    return "service status: degraded since 09:12, queue depth 4,812"


def big() -> str:
    """Read the long build log."""
    return "b" * 7_000


def huge() -> str:
    """Read the full crash dump."""
    return "c" * 20_000


def wide() -> str:
    """Read the raw trace file."""
    return "t" * 30_000


def greet(name: str) -> str:
    """Send a greeting to an on-call engineer.

    Args:
        name: Who to greet.
    """
    return f'hallo, {name} — ✓ <i>paged</i> & "acknowledged"'


def log_text(n: int) -> str:
    rows = [
        f"2026-09-{1 + (n + i) % 28:02d}T{(i * 7) % 24:02d}:{(i * 13) % 60:02d}|node-{(n * 31 + i) % 97:02d}"
        f"|req-{n:03d}{i:04d}|{(n * 6151 + i * 92821) % 100000 / 100:.2f}ms|svc-{i % 19}"
        for i in range(80)
    ]
    return f"log page {n}\n" + "\n".join(rows)


def logs(n: int) -> str:
    """Read one page of the request log.

    Args:
        n: The page number, from 1.
    """
    return log_text(n)


def search(query: str, limit: int = 5) -> str:
    """Search the runbook.

    Args:
        query: What to look for.
        limit: The most results to return.
    """
    return f"{min(limit, 3)} runbook hits for {query!r}: restart, drain, rollback"


def ticket(key: str) -> dict[str, Any]:
    """Look up a ticket by key.

    Args:
        key: The ticket key.
    """
    return {"key": key, "state": "investigating", "latencies": [312.5, 288.0], "assignee": None}


def tally(items: list[str]) -> int:
    """Count items.

    Args:
        items: The items.
    """
    return len(items)


def flaky(attempt: int) -> str:
    """A probe that fails on its first attempt.

    Args:
        attempt: The attempt number, from 0.
    """
    if attempt < 1:
        raise ModelRetry("probe again with attempt=1")
    return "probe succeeded on attempt 1"


def broken() -> str:
    """A tool whose backend is down."""
    raise RuntimeError("backend unavailable")


def snapshot() -> list[Any]:
    """Take a dashboard snapshot (an image plus a caption)."""
    return [BinaryContent(data=b"\x89PNG\r\n\x1a\n0000", media_type="image/png"), "dashboard at 09:30"]


def diagram() -> ImageUrl:
    """Fetch the topology diagram."""
    return ImageUrl(url="https://example.com/topology.png")


TOOLS: list[Callable[..., Any]] = [
    status,
    greet,
    big,
    huge,
    wide,
    logs,
    search,
    ticket,
    tally,
    flaky,
    broken,
    snapshot,
    diagram,
]
DENSE = [log_text(n) for n in range(1, 13)]


# ---------------------------------------------------------------------- the scripted model


Step = ModelResponse | Callable[[list[ModelMessage], AgentInfo], ModelResponse]


def call(tool: str, args: dict[str, Any], call_id: str, thought: str = "") -> ModelResponse:
    parts: list[Any] = [TextPart(thought)] if thought else []
    return ModelResponse(parts=[*parts, ToolCallPart(tool, args, call_id)])


def calls(*items: tuple[str, dict[str, Any], str], thought: str = "") -> ModelResponse:
    parts: list[Any] = [TextPart(thought)] if thought else []
    return ModelResponse(parts=[*parts, *(ToolCallPart(t, a, i) for t, a, i in items)])


def edit(call_id: str, *ops: dict[str, Any], thought: str = "") -> ModelResponse:
    return call(EDIT, {"ops": list(ops)}, call_id, thought)


def text(content: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content)])


def request_chars(messages: Sequence[ModelMessage], info: AgentInfo) -> int:
    """The scripted provider's own size of a request: its messages, instructions and tool definitions."""
    stripped = [dataclasses.replace(m, instructions=None) if isinstance(m, ModelRequest) else m for m in messages]
    tools = [
        {"name": t.name, "description": t.description, "parameters": t.parameters_json_schema}
        for t in [*info.function_tools, *info.output_tools]
    ]
    return len(ModelMessagesTypeAdapter.dump_json(stripped)) + len(info.instructions or "") + len(json.dumps(tools))


def dense_chars(messages: Sequence[ModelMessage]) -> int:
    prompt = "".join(
        str(p.content)
        for m in messages
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, UserPromptPart)
    )
    return sum(prompt.count(e) * len(e) for e in {json.dumps(d)[1:-1] for d in DENSE})


class Script:
    """Plays scripted responses, and records what the model saw on every call.

    `usage`: None leaves usage to `FunctionModel` (a scripted model: the shared estimator never calibrates on it). A
    `(other, dense)` pair of characters-per-token reports realistic input tokens instead, log pages at `dense` and
    everything else at `other`, from a model whose `system` isn't a scripted one, so the estimator calibrates on it
    as it would on a provider. `inflate` adds tokens to the reported input from a given call (0-based) on.
    """

    def __init__(
        self,
        steps: list[Step],
        *,
        usage: tuple[float, float] | None = (2.4, 1.7),
        inflate: tuple[int, int] | None = None,
    ) -> None:
        self.steps, self.usage, self.inflate = steps, usage, inflate
        self.seen: list[dict[str, Any]] = []

    def respond(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        index = len(self.seen)
        self.seen.append(
            {
                "messages": dump(messages),
                "instructions": info.instructions,
                "parameters": dump(info.model_request_parameters),
                "settings": dump(info.model_settings),
            }
        )
        if index >= len(self.steps):
            raise AssertionError(f"the script has no response for call {index}")
        step = self.steps[index]
        response = step(messages, info) if callable(step) else step
        response = dataclasses.replace(response, parts=list(response.parts))
        if self.usage is not None:
            other, dense = self.usage
            total = request_chars(messages, info)
            heavy = min(dense_chars(messages), total)
            tokens = math.ceil(heavy / dense + (total - heavy) / other)
            if self.inflate is not None and index >= self.inflate[0]:
                tokens += self.inflate[1]
            response.usage = RequestUsage(input_tokens=tokens, output_tokens=10)
        self.seen[-1]["reported_input_tokens"] = response.usage.input_tokens if self.usage is not None else None
        return response

    def model(self) -> FunctionModel:
        model = FunctionModel(self.respond, model_name="scripted")
        if self.usage is not None:
            model = RealUsageModel(self.respond, model_name="scripted")
        return model


class RealUsageModel(FunctionModel):
    @property
    def system(self) -> str:
        return "scripted-usage"  # not "function" or "test", so its usage calibrates the estimator


# ---------------------------------------------------------------------- running a scenario


@dataclasses.dataclass
class Turn:
    prompt: str | list[Any]
    conversation_id: str | None = "conv-1"
    history: str = "previous"  # "previous": this conversation's last all_messages(); "none": no message history


class Answer(BaseModel):
    answer: str
    confidence: float


@frozen_clock()
def run_scenario(
    factory: StackFactory,
    script: Script,
    turns: list[Turn],
    settings: Settings | None = None,
    *,
    instructions: str | None = None,
    output_type: Any = str,
) -> dict[str, Any]:
    settings = settings or Settings()
    records: list[dict[str, Any]] = []
    stack = factory(settings, records.append)
    agent = Agent(
        script.model(),
        system_prompt=SYSTEM,
        instructions=instructions,
        tools=TOOLS,
        capabilities=stack.capabilities,
        output_type=output_type,
    )
    runs: list[dict[str, Any]] = []
    histories: dict[str | None, list[ModelMessage]] = {}
    for turn in turns:
        stack.before_run()
        seen_before = len(script.seen)
        try:
            result = asyncio.run(
                agent.run(
                    turn.prompt,
                    message_history=histories.get(turn.conversation_id) if turn.history == "previous" else None,
                    conversation_id=turn.conversation_id,
                    usage_limits=UsageLimits(request_limit=settings.call_cap),
                )
            )
        except Exception as error:  # the way a run ends is part of the behaviour
            runs.append(
                {
                    "prompt": dump(turn.prompt),
                    "calls": len(script.seen) - seen_before,
                    "error": f"{type(error).__name__}: {error}",
                }
            )
            break
        history = result.all_messages()
        histories[turn.conversation_id] = history
        runs.append(
            {
                "prompt": dump(turn.prompt),
                "calls": len(script.seen) - seen_before,
                "output": dump(result.output),
                "all_messages": dump(history),
            }
        )
    return {"calls": script.seen, "records": records, "runs": runs}


# ---------------------------------------------------------------------- the scenarios

LONG = "The incident review needs every alert matched to its root cause and owner. " * 6


def single_turn(factory: StackFactory) -> dict[str, Any]:
    """One turn with tool calls, a thought and an answer, from a scripted model (fixed-ratio estimates); non-ASCII
    text and '<' in outputs."""
    script = Script(
        [
            call("status", {}, "c1", thought="Check the status first."),
            call("greet", {"name": "Zoë Ångström"}, "c0", thought="Then page — politely ✓."),
            call("search", {"query": "drain", "limit": 2}, "c2"),
            call("big", {}, "c3"),
            text("The service is degraded."),
        ],
        usage=None,
    )
    return run_scenario(factory, script, [Turn("What is the service status?")])


def multi_turn(factory: StackFactory) -> dict[str, Any]:
    """Four turns: earlier requests and answers become protected entries; the task keeps the first request."""
    script = Script(
        [
            call("status", {}, "c1"),
            text("answer one: degraded"),
            call("ticket", {"key": "OPS-7"}, "c2"),
            text("answer two: OPS-7 is investigating"),
            text("answer three, no tools"),
            calls(("tally", {"items": ["a", "b", "c"]}, "c3"), ("search", {"query": "rollback"}, "c4")),
            text("answer four: 3"),
        ]
    )
    turns = [Turn("first question"), Turn("second question"), Turn("third question"), Turn("fourth question")]
    return run_scenario(factory, script, turns)


def repeated_request(factory: StackFactory) -> dict[str, Any]:
    """A later turn whose request equals the first one: no `Current request` section and no tN.request entry."""
    script = Script([text("one"), text("two"), text("three")])
    turns = [Turn("same question"), Turn("other question"), Turn("same question")]
    return run_scenario(factory, script, turns)


def edit_outcomes(factory: StackFactory) -> dict[str, Any]:
    """Every edit outcome: accepted (delete, replace, add_note), unchanged, each kind of rejection, invalid
    arguments (a retry), and an edit after a later step."""
    script = Script(
        [
            call("status", {}, "c1"),
            call("big", {}, "c2"),
            edit("e1", {"op": "delete", "ids": ["s2.obs"]}, thought="The build log isn't needed."),
            edit("e2", {"op": "replace", "id": "s1.obs", "body": "status: degraded"}),
            edit("e3", {"op": "add_note", "id": "n1", "body": "Remember: degraded since 09:12."}),
            edit("e4", {"op": "replace", "id": "n1", "body": "Remember: degraded since 09:12."}),
            edit("e5", {"op": "delete", "ids": ["nope", "s1.act"]}),
            edit("e6", {"op": "rename", "id": "n1"}),
            edit("e7", {"op": "replace", "id": "missing", "body": "x"}),
            edit("e8", {"op": "add_note", "id": "bad id!", "body": "x"}),
            edit("e9", {"op": "add_note", "id": "n1", "body": "duplicate"}),
            edit("e10", {"op": "add_note", "id": "n2", "body": "y" * 24_001}),
            edit("e11"),
            call(EDIT, {"ops": "not a list"}, "e12"),
            calls(("status", {}, "c3"), (EDIT, {"ops": [{"op": "delete", "ids": ["s3.act", "s3.obs"]}]}, "e14")),
            text("done editing"),
        ]
    )
    return run_scenario(factory, script, [Turn("Tidy as you go.")])


def delete_readd(factory: StackFactory) -> dict[str, Any]:
    """Ordered operations within one edit: delete then re-add the same id (a note at the end), add then replace,
    and add then delete (unchanged)."""
    script = Script(
        [
            call("status", {}, "c1"),
            edit("e1", {"op": "delete", "ids": ["s1.obs"]}, {"op": "add_note", "id": "s1.obs", "body": "re-added"}),
            edit("e2", {"op": "add_note", "id": "n1", "body": "v1"}, {"op": "replace", "id": "n1", "body": "v2"}),
            edit("e3", {"op": "add_note", "id": "tmp", "body": "x"}, {"op": "delete", "ids": ["tmp"]}),
            text("done"),
        ]
    )
    return run_scenario(factory, script, [Turn("Reorganise.")])


def coercions(factory: StackFactory) -> dict[str, Any]:
    """Loosely typed operations: a replace without a body, numeric ids and bodies, a delete without ids, and
    non-string ids in a delete list."""
    script = Script(
        [
            call("status", {}, "c1"),
            edit("e1", {"op": "replace", "id": "s1.obs"}),
            edit("e2", {"op": "add_note", "id": 42, "body": 7}),
            edit("e3", {"op": "delete"}),
            edit("e4", {"op": "delete", "ids": [7]}),
            edit("e5", {"op": "replace", "id": 42, "body": "x"}),
            text("done"),
        ]
    )
    return run_scenario(factory, script, [Turn("Coerce.")])


def unhashable_ids(factory: StackFactory) -> dict[str, Any]:
    """An edit whose ids hold objects passes the schema, then raises TypeError and ends the run (SPEC 13.11)."""
    script = Script(
        [
            call("status", {}, "c1"),
            edit("e1", {"op": "delete", "ids": [{"id": "s1.obs"}]}),
            text("not reached"),
        ]
    )
    return run_scenario(factory, script, [Turn("Crash.")])


def capped_edit_result(factory: StackFactory) -> dict[str, Any]:
    """The edit tool's result is capped at max_output_chars (head and tail kept)."""
    script = Script(
        [
            call("status", {}, "c1"),
            edit("e1", {"op": "delete", "ids": [f"missing-{i}" for i in range(30)]}),
            text("done"),
        ]
    )
    return run_scenario(factory, script, [Turn("Go.")], Settings(max_output_chars=60))


def protected(factory: StackFactory) -> dict[str, Any]:
    """Edits touching protected turn entries are refused whole; other edits work; a protected-looking id that
    isn't present falls through to the normal checks; add_note may use a protected-looking id, and the real answer
    then gets a suffixed, unprotected id."""
    script = Script(
        [
            call("status", {}, "c1"),
            text("answer one"),
            text("answer two"),
            edit(
                "e1",
                {"op": "delete", "ids": ["s1.obs", "t1.answer"]},
                {"op": "replace", "id": "t2.request", "body": "x"},
            ),
            edit("e2", {"op": "replace", "id": "t2.answer", "body": "short"}),
            edit("e3", {"op": "delete", "ids": ["t9.answer"]}),
            edit("e4", {"op": "delete", "ids": ["s1.obs"]}, {"op": "add_note", "id": "n1", "body": "kept: degraded"}),
            edit("e5", {"op": "add_note", "id": "t3.answer", "body": "a note with a turn-like id"}),
            text("answer three"),
            edit("e6", {"op": "delete", "ids": ["t3.answer"]}),
            edit("e7", {"op": "delete", "ids": ["t3.answer~2"]}),
            text("answer four"),
        ]
    )
    turns = [Turn("first question"), Turn("second question"), Turn("third question"), Turn("fourth question")]
    return run_scenario(factory, script, turns)


def parallel(factory: StackFactory) -> dict[str, Any]:
    """Parallel calls: all run; an edit in the middle; results in the order listed; a thought with calls."""
    script = Script(
        [
            calls(("status", {}, "c1"), ("search", {"query": "restart"}, "c2"), thought="Two reads at once."),
            calls(
                ("ticket", {"key": "A"}, "c3"),
                (EDIT, {"ops": [{"op": "delete", "ids": ["s1.obs"]}]}, "e1"),
                ("tally", {"items": ["x"]}, "c4"),
            ),
            calls((EDIT, {"ops": [{"op": "add_note", "id": "n1", "body": "two"}]}, "e2"), ("status", {}, "c5")),
            calls((EDIT, {"ops": [{"op": "add_note", "id": "n2", "body": "free"}]}, "e3")),
            text("done"),
        ]
    )
    return run_scenario(factory, script, [Turn("Read in parallel.")])


def step_count_drift(factory: StackFactory) -> dict[str, Any]:
    """The status's task-step count subtracts every edit call, while the enforced count frees only a response whose
    first call is an edit, so the shown count drifts below the enforced one, here below zero (SPEC 13.9). The two
    edits in one response are identical and both rejected, so their order (they may run concurrently) cannot change
    the transcript."""
    nope = {"ops": [{"op": "delete", "ids": ["nope"]}]}
    script = Script(
        [
            calls(("status", {}, "c1"), (EDIT, {"ops": [{"op": "add_note", "id": "n1", "body": "a"}]}, "e1")),
            calls((EDIT, nope, "e2"), (EDIT, nope, "e3")),
            calls((EDIT, {"ops": [{"op": "add_note", "id": "n3", "body": "c"}]}, "e4"), ("status", {}, "c2")),
            text("done"),
        ]
    )
    return run_scenario(factory, script, [Turn("Count steps.")], Settings(max_steps=3, call_cap=20))


def guard(factory: StackFactory) -> dict[str, Any]:
    """The output guard with realistic density: pages admitted until the next would pass the limit, then held
    back with everything after it; edits never held back; an edit freeing room before a refetch; r observed from
    reported usage."""
    script = Script(
        [
            call("status", {}, "c1"),
            calls(*((("logs", {"n": n}, f"p{n}")) for n in range(1, 4))),
            calls(*((("logs", {"n": n}, f"p{n}")) for n in range(4, 9)), ("status", {}, "c2")),
            calls(
                (EDIT, {"ops": [{"op": "delete", "ids": ["s2.obs", "s3.obs"]}]}, "e1"),
                ("logs", {"n": 6}, "p6b"),
                ("logs", {"n": 7}, "p7b"),
            ),
            call("huge", {}, "h1"),
            call("search", {"query": "x" * 120}, "c3"),
            text("pages read"),
        ]
    )
    return run_scenario(factory, script, [Turn("Read the request log.")], Settings(budget_tokens=12_000))


def dense_guard(factory: StackFactory) -> dict[str, Any]:
    """Pages denser than the guard's starting ratio (1.2 characters per token): the observed r falls below 1.5 and
    tightens the guard; a held-back call with long arguments is shortened in the note; a refetch is recorded once."""
    query = "latency regression " * 12
    script = Script(
        [
            call("logs", {"n": 1}, "p1"),
            call("logs", {"n": 2}, "p2"),
            calls(("huge", {}, "h1"), ("search", {"query": query}, "q1")),
            call("search", {"query": query}, "q2"),
            call("search", {"query": query}, "q3"),
            calls(("logs", {"n": 3}, "p3"), ("logs", {"n": 4}, "p4")),
            text("done"),
        ],
        usage=(2.4, 1.2),
    )
    return run_scenario(factory, script, [Turn("Read densely.")], Settings(budget_tokens=16_000))


def never_fits(factory: StackFactory) -> dict[str, Any]:
    """An output too large to fit however much the model tidies is held back on every call (SPEC 13.15)."""
    script = Script(
        [
            call("big", {}, "b1"),
            edit("e1", {"op": "delete", "ids": ["s1.act", "s1.obs"]}),
            call("big", {}, "b2"),
            text("gave up"),
        ]
    )
    return run_scenario(factory, script, [Turn("Read the build log.")], Settings(budget_tokens=6_000))


def pressure_and_over_limit(factory: StackFactory) -> dict[str, Any]:
    """Growth the guard doesn't cover (long thoughts): PRESSURE at 70% of the budget, then OVER LIMIT using the
    reserve; the allowance resets at the next turn, and a second request in a row over the limit ends the run."""
    script = Script(
        [
            call("status", {}, "c1", thought=LONG * 8),
            call("status", {}, "c2", thought=LONG * 8),
            call("status", {}, "c3", thought=LONG * 8),
            call("status", {}, "c4", thought=LONG),
            call("status", {}, "c5", thought=LONG * 4),
            text("answer one"),
            call("status", {}, "c6", thought=LONG * 4),
            text("not reached"),
        ]
    )
    turns = [Turn("first question"), Turn("second question")]
    return run_scenario(factory, script, turns, Settings(budget_tokens=9_000))


def over_limit_rearm(factory: StackFactory) -> dict[str, Any]:
    """Within one turn: a request over the limit uses the reserve; an edit brings the next request back within the
    limit, which re-arms the allowance; a later request over the limit uses the reserve again instead of ending."""
    script = Script(
        [
            call("status", {}, "c1", thought=LONG * 8),
            call("status", {}, "c2", thought=LONG * 8),
            call("status", {}, "c3", thought=LONG * 8),
            edit("e1", {"op": "delete", "ids": ["s1.act", "s2.act", "s3.act"]}),
            call("status", {}, "c4", thought=LONG * 8),
            call("status", {}, "c5", thought=LONG * 8),
            call("status", {}, "c6", thought=LONG * 8),
            edit("e2", {"op": "delete", "ids": ["s5.act", "s6.act", "s7.act"]}),
            text("done"),
        ]
    )
    return run_scenario(factory, script, [Turn("Think at length.")], Settings(budget_tokens=9_000))


def over_budget_reported(factory: StackFactory) -> dict[str, Any]:
    """end_over_budget: a call whose reported input is over the budget ends the session."""
    script = Script([call("status", {}, "c1"), call("status", {}, "c2"), text("x")], inflate=(1, 40_000))
    return run_scenario(factory, script, [Turn("Go.")], Settings(end_over_budget=True))


def over_budget_ignored(factory: StackFactory) -> dict[str, Any]:
    """Without end_over_budget (the default), the reported overshoot doesn't end the session at that call; the
    next request's estimate, anchored on the reported input, is over the budget itself, and that ends it."""
    script = Script([call("status", {}, "c1"), call("status", {}, "c2"), text("x")], inflate=(1, 40_000))
    return run_scenario(factory, script, [Turn("Go.")])


def step_limit(factory: StackFactory) -> dict[str, Any]:
    """Free edit steps, the finalize notice in the last steps, and the task-step limit."""
    steps: list[Step] = []
    for i in range(1, 4):
        steps += [call("status", {}, f"c{i}"), edit(f"e{i}", {"op": "add_note", "id": f"n{i}", "body": str(i)})]
    steps += [call("status", {}, f"d{i}") for i in range(1, 6)]
    script = Script(steps)
    return run_scenario(factory, script, [Turn("Keep going.")], Settings(max_steps=6, call_cap=40))


def call_cap(factory: StackFactory) -> dict[str, Any]:
    """The model-call cap counts every call, edit steps included, and its notice counts down too."""
    steps: list[Step] = [edit(f"e{i}", {"op": "add_note", "id": f"n{i}", "body": str(i)}) for i in range(1, 9)]
    script = Script(steps)
    return run_scenario(factory, script, [Turn("Edit away.")], Settings(max_steps=60, call_cap=5))


def call_cap_resets_per_run(factory: StackFactory) -> dict[str, Any]:
    """The call cap and the step limit are per run (turn)."""
    script = Script(
        [
            call("status", {}, "c1"),
            call("status", {}, "c2"),
            text("one"),
            call("status", {}, "c3"),
            call("status", {}, "c4"),
            text("two"),
        ]
    )
    return run_scenario(factory, script, [Turn("first"), Turn("second")], Settings(max_steps=4, call_cap=4))


def tool_returns(factory: StackFactory) -> dict[str, Any]:
    """Structured tool returns, a ModelRetry, an unknown tool and a validation error, as rendered observations."""
    script = Script(
        [
            calls(("ticket", {"key": "K1"}, "c1"), ("tally", {"items": ["a", "b"]}, "c2")),
            call("flaky", {"attempt": 0}, "c3"),
            call("flaky", {"attempt": 1}, "c4"),
            call("no_such_tool", {}, "c5"),
            call("tally", {"items": "not a list"}, "c6"),
            text("done"),
        ]
    )
    return run_scenario(factory, script, [Turn("Use the tools.")])


def tool_raises(factory: StackFactory) -> dict[str, Any]:
    """A tool that raises (other than ModelRetry) ends the run (SPEC 13.12)."""
    script = Script([call("status", {}, "c1"), call("broken", {}, "c2"), text("not reached")])
    return run_scenario(factory, script, [Turn("Use the broken tool.")])


def images(factory: StackFactory) -> dict[str, Any]:
    """Non-text content: an image tool return renders as an empty observation, a mixed list keeps its text, and an
    image in the prompt is dropped from the task (SPEC 13.10)."""
    script = Script(
        [
            call("snapshot", {}, "c1"),
            call("diagram", {}, "c2"),
            text("seen"),
        ]
    )
    prompt = ["Describe the dashboard.", BinaryContent(data=b"\x89PNG\r\n\x1a\n1111", media_type="image/png")]
    return run_scenario(factory, script, [Turn(prompt)])


def conversations(factory: StackFactory) -> dict[str, Any]:
    """Two conversations on one agent keep separate contexts; a third turn without history in conversation 1."""
    script = Script(
        [
            call("status", {}, "a1"),
            text("conversation one, turn one"),
            call("ticket", {"key": "B"}, "b1"),
            text("conversation two, turn one"),
            text("conversation one, turn two"),
            text("conversation two, turn two"),
            text("conversation one, fresh history"),
        ]
    )
    turns = [
        Turn("one: first", "conv-1"),
        Turn("two: first", "conv-2"),
        Turn("one: second", "conv-1"),
        Turn("two: second", "conv-2"),
        Turn("one: third", "conv-1", history="none"),
    ]
    return run_scenario(factory, script, turns)


def no_conversation_id(factory: StackFactory) -> dict[str, Any]:
    """Runs without a conversation id: the id comes from the message history, as Pydantic AI resolves it."""
    script = Script([call("status", {}, "c1"), text("one"), text("two"), text("three")])
    turns = [Turn("first", None), Turn("second", None), Turn("third", None, history="none")]
    return run_scenario(factory, script, turns)


def agent_instructions(factory: StackFactory) -> dict[str, Any]:
    """The agent's own instructions alongside ClmContext's."""
    script = Script([call("status", {}, "c1"), text("done")])
    return run_scenario(factory, script, [Turn("Go.")], instructions="Answer in one sentence.")


def structured_output(factory: StackFactory) -> dict[str, Any]:
    """A structured output type: the output tool's call is a step like any other."""
    script = Script(
        [
            call("status", {}, "c1"),
            call("final_result", {"answer": "degraded", "confidence": "high"}, "o1"),
            call("final_result", {"answer": "degraded", "confidence": 0.9}, "o2"),
        ]
    )
    return run_scenario(factory, script, [Turn("Answer as structured output.")], output_type=Answer)


def limits(factory: StackFactory) -> dict[str, Any]:
    """Smaller context limits: entry count, body size and total size, in the instructions and in validation."""
    script = Script(
        [
            call("status", {}, "c1"),
            edit("e1", *({"op": "add_note", "id": f"n{i}", "body": "z"} for i in range(6))),
            edit("e2", {"op": "add_note", "id": "n1", "body": "z" * 300}),
            edit(
                "e3", {"op": "add_note", "id": "n1", "body": "z" * 90}, {"op": "add_note", "id": "n2", "body": "z" * 90}
            ),
            text("done"),
        ]
    )
    settings = Settings(max_entries=6, max_body_chars=200, max_total_chars=150)
    return run_scenario(factory, script, [Turn("Go.")], settings)


def file_bytes_limit(factory: StackFactory) -> dict[str, Any]:
    """The serialized-file byte limit (indented JSON, measured in UTF-8 bytes) rejects edits that the character
    limits allow."""
    script = Script(
        [
            call("status", {}, "c1"),
            edit("e1", {"op": "add_note", "id": "n1", "body": "€" * 900}),
            edit("e2", {"op": "add_note", "id": "n1", "body": "e" * 900}),
            text("done"),
        ]
    )
    return run_scenario(factory, script, [Turn("Go.")], Settings(max_file_bytes=2_000))


def oversize_entry(factory: StackFactory) -> dict[str, Any]:
    """A tool output longer than the per-entry body limit is admitted (it fits the budget), and every edit is then
    rejected by validation until one deletes that entry."""
    script = Script(
        [
            call("wide", {}, "w1"),
            edit("e1", {"op": "add_note", "id": "n1", "body": "a note"}),
            edit("e2", {"op": "delete", "ids": ["s1.obs"]}, {"op": "add_note", "id": "n1", "body": "a note"}),
            text("done"),
        ]
    )
    return run_scenario(factory, script, [Turn("Read the raw trace.")])


def long_session(factory: StackFactory) -> dict[str, Any]:
    """A longer session at the default budget: pages, tidying, notes and answers over three turns."""
    steps: list[Step] = [
        calls(("logs", {"n": 1}, "p1"), ("logs", {"n": 2}, "p2"), ("logs", {"n": 3}, "p3")),
        calls(("logs", {"n": 4}, "p4"), ("logs", {"n": 5}, "p5"), ("logs", {"n": 6}, "p6")),
        edit(
            "e1",
            {"op": "add_note", "id": "findings", "body": "pages 1-6 read; slow nodes noted"},
            {"op": "delete", "ids": ["s1.obs"]},
        ),
        calls(("logs", {"n": 7}, "p7"), ("logs", {"n": 8}, "p8"), ("logs", {"n": 9}, "p9"), ("logs", {"n": 10}, "p10")),
        edit(
            "e3",
            {"op": "delete", "ids": ["s2.obs", "s4.obs"]},
            {"op": "replace", "id": "findings", "body": "1-10 done"},
        ),
        calls(("logs", {"n": 11}, "p11"), ("logs", {"n": 12}, "p12")),
        text("Turn one: all 12 pages reviewed."),
        call("ticket", {"key": "OPS-0010003"}, "c1"),
        text("Turn two: OPS-0010003 is investigating."),
        edit("e4", {"op": "delete", "ids": ["s6.obs", "s7.act", "s7.obs"]}),
        calls(("logs", {"n": 3}, "p3b"), ("search", {"query": "svc-4"}, "c2")),
        text("Turn three: svc-4 appears on page 3."),
    ]
    script = Script(steps)
    turns = [Turn("Review the request log."), Turn("Is OPS-0010003 still open?"), Turn("Where does svc-4 appear?")]
    return run_scenario(factory, script, turns)


SCENARIOS: dict[str, Callable[[StackFactory], dict[str, Any]]] = {
    f.__name__: f
    for f in (
        single_turn,
        multi_turn,
        repeated_request,
        edit_outcomes,
        delete_readd,
        coercions,
        unhashable_ids,
        capped_edit_result,
        protected,
        parallel,
        step_count_drift,
        guard,
        dense_guard,
        never_fits,
        pressure_and_over_limit,
        over_limit_rearm,
        over_budget_reported,
        over_budget_ignored,
        step_limit,
        call_cap,
        call_cap_resets_per_run,
        tool_returns,
        tool_raises,
        images,
        conversations,
        no_conversation_id,
        agent_instructions,
        structured_output,
        limits,
        file_bytes_limit,
        oversize_entry,
        long_session,
    )
}
