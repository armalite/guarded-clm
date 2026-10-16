"""`ClmContext`: the model manages its own working context, within a token budget.

It combines structured edits and parallel calls, the output guard with its conservative size estimate, and protected
turn entries. SPEC.md describes the behaviour; tests/test_golden.py requires the recorded transcripts to stay
identical.

Every request is rebuilt from the working context: the system prompt, then one user message with `<task>` (the
first request, plus the current one in later turns), `<working_context>` (the entries, one JSON object per line) and
`<runtime_status>` (the step, the calls, the size against the limit, and any notices).
"""

import dataclasses
import json
import math
import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from guarded_clm.context import (
    ContextLimits,
    ContextStore,
    ContextValidationError,
    EditOutcome,
    Entry,
    parse_candidate,
    render_entries,
    serialize,
)
from guarded_clm.errors import BudgetExceededError, ContextLimitError
from guarded_clm.estimate import (
    RESERVE_TOKENS,
    ConversationStore,
    SizeEstimator,
    enforced_limit,
    part_text,
    reported_overshoot,
)
from guarded_clm.instructions import instructions
from guarded_clm.steps import EDIT_TOOL, NOTE

if TYPE_CHECKING:
    from pydantic_ai.capabilities import ModelRequestContext

VARIANT = "clm_lite_v3_3"  # the `variant` of every trace record: the tested design's name (a compatibility contract)
PRESSURE_RATIO = 0.70  # of the budget
TASK_OPEN = "<task>\n"
R_CAP = 1.5  # characters per token: the guard's highest new-text ratio, and its ratio before any observation
MARGIN = 1.10
SHORT_ARGS = 80
HELD_BACK = (
    "Held back: {tool}({args}), about {tokens:,} tokens; only {free:,} tokens free under the limit. Tidy your context, "
    "then call it again."
)
PROTECTED_ID = re.compile(r"^t\d+\.(request|answer)$")


def is_protected(entry_id: str) -> bool:
    return PROTECTED_ID.match(entry_id) is not None


def render_marked(entries: tuple[Entry, ...] | list[Entry]) -> str:
    """The entries as JSON lines, with `"protected": true` on protected entries and '<' escaped."""
    lines = []
    for e in entries:
        item: dict[str, Any] = {"id": e.id, "role": e.role, "body": e.body}
        if is_protected(e.id):
            item["protected"] = True
        lines.append(json.dumps(item, ensure_ascii=False).replace("<", "\\u003c"))
    return "\n".join(lines)


def cap(text: str, limit: int) -> str:
    """The output cap: keep the head and the tail."""
    if len(text) <= limit:
        return text
    marker = f"\n[... {len(text) - limit} characters omitted ...]\n"
    keep = max(limit - len(marker), 0)
    head = keep * 3 // 5
    return text[:head] + marker + text[len(text) - (keep - head) :]


def receipt(outcome: EditOutcome) -> str:
    """clm-lib's receipt: ids and counts only, never the removed content."""
    if outcome.status != "accepted" or outcome.before is None or outcome.after is None:
        detail = f": {outcome.reason}" if outcome.reason else ""
        return f"context.json {outcome.status.replace('_', ' ')}{detail}; the previous context is kept."
    before = {e.id: e for e in outcome.before.entries}
    after = {e.id: e for e in outcome.after.entries}
    removed = [i for i in before if i not in after]
    added = [i for i in after if i not in before]
    rewritten = [i for i in after if i in before and after[i] != before[i]]
    return (
        f"context.json accepted as revision {outcome.after.number}: {len(removed)} removed {removed[:20]}, "
        f"{len(added)} added {added[:20]}, {len(rewritten)} rewritten {rewritten[:20]}."
    )


def short_args(call: ToolCallPart) -> str:
    text = call.args_as_json_str()
    return text if len(text) <= SHORT_ARGS else text[: SHORT_ARGS - 1] + "…"


@dataclasses.dataclass
class Session:
    """Per conversation: the context store and the runtime's own state."""

    store: ContextStore = dataclasses.field(default_factory=ContextStore)
    step: int = 0
    first_task: str | None = None
    current_request: str | None = None
    turn: int = 0
    over_limit: bool = False  # the previous request used the reserve
    pending_receipt: str | None = None
    calls_this_run: int = 0
    edit_steps: int = 0  # edit_context calls this session (each counts, whether or not it was the step's first call)
    run_steps: int = 0  # `step` at the start of this run (turn)
    run_edit_steps: int = 0  # `edit_steps` at the start of this run (turn)


@dataclasses.dataclass
class GuardState:
    """Per session: the entries before the current response's tools ran, and after each of its edits."""

    before: tuple[Entry, ...] | None = None
    after_edit: dict[str, tuple[Entry, ...]] = dataclasses.field(default_factory=dict)
    held: set[tuple[str, str]] = dataclasses.field(default_factory=set)  # (tool, args) held back so far


@dataclasses.dataclass
class Density:
    """Per session: the lowest observed new-text ratio, and the last answered request (chars, entry lines, task,
    reported tokens) to measure the next one against."""

    lowest: float = math.inf
    sent: tuple[int, tuple[str, ...], str] | None = None
    answered: tuple[int, tuple[str, ...], str, int] | None = None

    @property
    def r(self) -> float:
        return min(R_CAP, self.lowest)


class ClmContext(AbstractCapability[Any]):
    """The model manages its own working context within a token budget (`clm_lite_v3_3`).

    Use it with `ClmStepLimit` in front of it, as tested: `clm_context()` builds both. See SPEC.md.

    Args:
        budget_tokens: The context budget. The enforced limit is the budget minus a 2,048-token reserve.
        end_over_budget: End the session (`BudgetExceededError`) when a call's reported input is over the budget.
        max_output_chars: The cap on the `edit_context` tool's result.
        max_steps: The task-step limit shown in the runtime status (`ClmStepLimit` enforces it).
        call_cap: The model-call cap shown in the runtime status (`ClmStepLimit` enforces it). 144 by default, as
            in `clm_lite_v3_3`, whatever `max_steps` is; `clm_context()` passes the step limit's cap.
        limits: The working context's entry, body and total limits.
        on_record: Called with each trace record (a dict), for example to write JSONL or OpenTelemetry events.
        id: The capability id, unique within an agent's capabilities. It isn't sent to the model.
        strict_ratio_learning: The shared estimator learns its new-text ratio only from a request that extends the
            last answered one, and ignores an observation below 0.5 characters per token (decisions P6). Off by
            default, as tested.
    """

    def __init__(
        self,
        *,
        budget_tokens: int = 32_000,
        end_over_budget: bool = False,
        max_output_chars: int = 8_000,
        max_steps: int = 60,
        call_cap: int = 144,
        limits: ContextLimits | None = None,
        on_record: Callable[[dict[str, Any]], None] | None = None,
        id: str = "clm_context",
        strict_ratio_learning: bool = False,
    ) -> None:
        self.id = id
        self.description = "The model manages its own working context within a token budget."
        self.defer_loading = False
        self.budget_tokens = budget_tokens
        self.limit = enforced_limit(budget_tokens)
        self.end_over_budget = end_over_budget
        self.max_output_chars = max_output_chars
        self.max_steps = max_steps
        self.call_cap = call_cap
        self.limits = limits or ContextLimits()
        self.on_record = on_record
        self.strict_ratio_learning = strict_ratio_learning
        # Per conversation, shared by `for_run`'s clones.
        self.sessions: ConversationStore[Session] = ConversationStore(lambda: Session(store=ContextStore(self.limits)))
        self.estimators: ConversationStore[SizeEstimator] = ConversationStore(SizeEstimator)
        self.guards: dict[int, GuardState] = {}  # by session object
        self.densities: dict[int, Density] = {}  # by session object
        # Per run.
        self.session = Session(store=ContextStore(self.limits))
        self.estimator = SizeEstimator()
        self.request_ctx: tuple[RunContext[Any], ModelRequestParameters] | None = None
        self.last_check: dict[str, Any] = {}

    def record(self, entry: dict[str, Any]) -> None:
        if entry.get("type") == "lite_held_back":
            entry = {**entry, **self.last_check}
        if self.on_record is not None:
            self.on_record({"variant": VARIANT, **entry})

    async def for_run(self, ctx: RunContext[Any]) -> "ClmContext":
        clone = ClmContext(
            budget_tokens=self.budget_tokens,
            end_over_budget=self.end_over_budget,
            max_output_chars=self.max_output_chars,
            max_steps=self.max_steps,
            call_cap=self.call_cap,
            limits=self.limits,
            on_record=self.on_record,
            id=self.id or "clm_context",  # the same id, so `resolve()` finds this clone
            strict_ratio_learning=self.strict_ratio_learning,
        )
        key = ctx.conversation_id or ctx.run_id or "session"
        clone.sessions, clone.estimators = self.sessions, self.estimators
        clone.session, clone.estimator = self.sessions.get(key), self.estimators.get(key)
        clone.guards, clone.densities = self.guards, self.densities
        session = clone.session
        session.calls_this_run = 0
        session.run_steps, session.run_edit_steps = session.step, session.edit_steps
        return clone

    def guard(self) -> GuardState:
        return self.guards.setdefault(id(self.session), GuardState())

    def density(self) -> Density:
        return self.densities.setdefault(id(self.session), Density())

    def get_instructions(self) -> str | None:
        return instructions(self.limits)

    # ------------------------------------------------------------------ the edit tool

    def get_toolset(self) -> FunctionToolset[Any] | None:
        def edit_context(ctx: RunContext[Any], ops: list[dict[str, Any]]) -> str:
            """Apply operations to your working context, in order.

            Args:
                ops: A list of {"op": "delete", "ids": [...]}, {"op": "replace", "id": ..., "body": ...} or
                    {"op": "add_note", "id": ..., "body": ...}.
            """
            bound = self.resolve(ctx)
            result = bound.apply_ops(ops)
            if ctx.tool_call_id is not None:
                bound.guard().after_edit[ctx.tool_call_id] = bound.session.store.entries
            return result

        return FunctionToolset(tools=[edit_context], id="clm_context_tools")

    def resolve(self, ctx: RunContext[Any]) -> "ClmContext":
        bound = ctx.capabilities.get(self.id or "")
        return bound if isinstance(bound, ClmContext) else self

    def apply_ops(self, ops: list[dict[str, Any]]) -> str:
        """Apply an edit: all operations or none. An edit that deletes or replaces a protected entry is refused."""
        session = self.session
        present = {e.id for e in session.store.entries}
        touched: list[str] = []
        for op in ops:
            if op.get("op") == "delete":
                touched += [i for i in (op.get("ids") or []) if isinstance(i, str) and is_protected(i) and i in present]
            elif op.get("op") == "replace" and isinstance(op.get("id"), str) and is_protected(op["id"]):
                touched += [op["id"]] if op["id"] in present else []
        session.edit_steps += 1  # an edit step is free
        if touched:
            protected = sorted(set(touched))
            outcome = EditOutcome("rejected", f"protected ids {protected}", before=session.store.current)
            session.pending_receipt = receipt(outcome)
            self.record_edit(outcome, ops)
            self.record({"type": "lite_protected_refused", "step": session.step, "ids": protected})
            return cap(f"{outcome.status}: {len(ops)} operation(s); {outcome.reason}", self.max_output_chars)
        before = session.store.current
        entries = list(session.store.entries)
        try:
            for op in ops:
                kind = op.get("op")
                if kind == "delete":
                    ids = set(op.get("ids") or [])
                    unknown = ids - {e.id for e in entries}
                    if unknown:
                        raise ContextValidationError(f"unknown ids {sorted(unknown)[:5]}")
                    entries = [e for e in entries if e.id not in ids]
                elif kind == "replace":
                    target = next((e for e in entries if e.id == op.get("id")), None)
                    if target is None:
                        raise ContextValidationError(f"unknown id {op.get('id')!r}")
                    entries = [Entry(e.id, e.role, str(op.get("body", ""))) if e is target else e for e in entries]
                elif kind == "add_note":
                    entries.append(Entry(str(op.get("id", "")), "note", str(op.get("body", ""))))
                else:
                    raise ContextValidationError(f"unknown op {kind!r}")
            parse_candidate(serialize(entries).encode("utf-8"), self.limits)
        except ContextValidationError as exc:
            outcome = EditOutcome("rejected", str(exc), before=before)
        else:
            if [e.to_dict() for e in entries] == [e.to_dict() for e in before.entries]:
                outcome = EditOutcome("unchanged_write", before=before)
            else:
                outcome = EditOutcome(
                    "accepted", before=before, after=session.store.commit(entries, "model_edit", session.step)
                )
        session.pending_receipt = receipt(outcome)
        self.record_edit(outcome, ops)
        return cap(
            f"{outcome.status}: {len(ops)} operation(s)" + (f"; {outcome.reason}" if outcome.reason else ""),
            self.max_output_chars,
        )

    def record_edit(self, outcome: EditOutcome, ops: list[dict[str, Any]]) -> None:
        session = self.session
        self.record(
            {
                "type": "lite_edit",
                "step": session.step,
                "status": outcome.status,
                "reason": outcome.reason,
                "revision": session.store.current.number,
                "exit_code": 0,
                "timed_out": False,
                "code": json.dumps(ops),
                "entries": len(session.store.entries),
            }
        )

    # ------------------------------------------------------------------ folding a step into the context

    def absorb(self, messages: list[ModelMessage]) -> tuple[SystemPromptPart | None, list[str]]:
        """Fold everything since the last rebuilt request into the context store: completed steps (action,
        observation with the guard applied, receipt), a turn's final answer, and a new turn's request. Returns the
        system prompt part and the notes to show in the status."""
        system = next(
            (p for m in messages if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, SystemPromptPart)),
            None,
        )
        start = 0
        for index, message in enumerate(messages):
            if isinstance(message, ModelRequest) and any(
                isinstance(p, UserPromptPart) and part_text(p).startswith(TASK_OPEN) for p in message.parts
            ):
                start = index + 1
        session, notes = self.session, []
        new = messages[start:]
        pending_notes = [
            part_text(p)
            for m in new
            if isinstance(m, ModelRequest)
            for p in m.parts
            if isinstance(p, UserPromptPart) and part_text(p).startswith(NOTE)
        ]
        for index, message in enumerate(new):
            if isinstance(message, ModelResponse):
                calls = [p for p in message.parts if isinstance(p, ToolCallPart)]
                thought = " ".join(p.content for p in message.parts if isinstance(p, TextPart)).strip()
                if not calls:
                    # A turn's final reply: kept as an entry once the next turn starts.
                    if thought:
                        session.store.append(
                            [Entry(f"t{session.turn}.answer", "assistant", thought)], "runtime", session.step
                        )
                    continue
                session.step += 1
                acts = [f"thought: {thought}"] if thought else []
                acts += [f"{c.tool_name}({c.args_as_json_str()})" for c in calls]
                following = new[index + 1] if index + 1 < len(new) else None
                returns = (
                    [p for p in following.parts if isinstance(p, ToolReturnPart | RetryPromptPart)]
                    if (isinstance(following, ModelRequest))
                    else []
                )
                act = Entry(f"s{session.step}.act", "assistant", "\n".join(acts))
                results = self.guarded_results(calls, returns, act, system, pending_notes)
                entries = [act, Entry(f"s{session.step}.obs", "observation", "\n\n".join(results) or "(no output)")]
                if session.pending_receipt:
                    entries.append(Entry(f"s{session.step}.rcpt", "receipt", session.pending_receipt))
                    session.pending_receipt = None
                session.store.append(entries, "runtime", session.step)
            elif isinstance(message, ModelRequest):
                for part in message.parts:
                    if not isinstance(part, UserPromptPart):
                        continue
                    text = part_text(part)
                    if text.startswith(NOTE):
                        notes.append(text)
                    elif not text.startswith(TASK_OPEN):
                        self.new_turn(text)
        return system, notes

    def new_turn(self, request: str) -> None:
        session = self.session
        if session.first_task is None:
            session.first_task = request
        elif session.current_request is not None and session.current_request != session.first_task:
            # The previous turn's request becomes a (protected) entry; the new one is the current request.
            session.store.append(
                [Entry(f"t{session.turn}.request", "observation", session.current_request)], "runtime", session.step
            )
        session.turn += 1
        session.current_request = request
        session.over_limit = False  # the over-limit allowance resets at each new user turn

    def guarded_results(
        self,
        calls: list[ToolCallPart],
        returns: list[ToolReturnPart | RetryPromptPart],
        act: Entry,
        system: SystemPromptPart | None,
        notes: list[str],
    ) -> list[str]:
        """The step's observation blocks, in the order the calls were listed, with outputs that don't fit held back."""
        session, state = self.session, self.guard()
        by_id = {p.tool_call_id: p for p in returns}
        ordered = [(c, by_id.pop(c.tool_call_id, None)) for c in calls] + [(None, p) for p in by_id.values()]
        receipt = (
            [Entry(f"s{session.step}.rcpt", "receipt", session.pending_receipt)] if session.pending_receipt else []
        )
        context = state.before if state.before is not None else session.store.entries
        results: list[str] = []
        holding = False
        for call, part in ordered:
            if part is None:
                continue
            name = part.tool_name if isinstance(part, ToolReturnPart) else part.tool_name or "retry"
            text = part.model_response_str() if isinstance(part, ToolReturnPart) else part.model_response()
            block = f"[{name}]\n{text}"
            key = (call.tool_name, call.args_as_json_str()) if call is not None else None
            if key is not None and key in state.held:
                self.record({"type": "lite_refetch", "step": session.step, "tool": key[0], "args": key[1]})
                state.held.discard(key)
            if call is not None and call.tool_name == EDIT_TOOL:
                results.append(block)  # never held back
                context = state.after_edit.get(call.tool_call_id, context)
                continue

            def size(blocks: list[str], context: tuple[Entry, ...] = context) -> int:
                obs = Entry(f"s{session.step}.obs", "observation", "\n\n".join(blocks) or "(no output)")
                return self.measure_entries([*context, act, obs, *receipt], system, notes)

            if not holding:
                with_it = size([*results, block])
                if with_it <= self.limit:
                    results.append(block)
                    continue
                holding = True
            without = size(results)
            tokens = max(size([*results, block]) - without, 0)
            free = max(self.limit - without, 0)
            args = short_args(call) if call is not None else ""
            results.append(f"[{name}]\n" + HELD_BACK.format(tool=name, args=args, tokens=tokens, free=free))
            if key is not None:
                state.held.add(key)
            self.record(
                {
                    "type": "lite_held_back",
                    "step": session.step,
                    "tool": name,
                    "args": key[1] if key else None,
                    "estimated_tokens": tokens,
                    "free_tokens": free,
                }
            )
        return results

    # ------------------------------------------------------------------ the request

    def task_text(self) -> str:
        session = self.session
        first = session.first_task or ""
        if session.current_request and session.current_request != first:
            return f"{first}\n\nCurrent request:\n{session.current_request}"
        return first

    def render(self, status: str) -> str:
        return self.render_with(self.session.store.entries, status)

    def render_with(self, entries: tuple[Entry, ...] | list[Entry], status: str) -> str:
        return (
            f"{TASK_OPEN}{self.task_text().strip()}\n</task>\n\n<working_context>\n"
            f"{render_marked(entries)}\n</working_context>\n\n"
            f"<runtime_status>\n{status.strip()}\n</runtime_status>\n"
        )

    def status(self, tokens: int, notices: list[str]) -> str:
        session = self.session
        task_steps = (session.step - session.run_steps) - (session.edit_steps - session.run_edit_steps)
        lines = [
            f"step {session.step + 1}; task steps used this turn {task_steps} of {self.max_steps} (edit_context "
            f"steps are free); model calls this turn {session.calls_this_run} of {self.call_cap}",
            f"request size: about {tokens:,} of {self.limit:,} tokens ({round(100 * tokens / self.limit)}% of the "
            f"limit); budget {self.budget_tokens:,}",
            f"context revision {session.store.current.number}, {len(session.store.entries)} entries",
        ]
        return "\n".join([*lines, *notices])

    def pressure(self, tokens: int) -> str:
        return (
            f"PRESSURE: the request is about {tokens:,} tokens, {round(100 * tokens / self.budget_tokens)}% of "
            f"the {self.budget_tokens:,}-token budget. Manage the working context before it runs out."
        )

    # ------------------------------------------------------------------ measuring a would-be request (the guard)

    def shared_measure(self, entries: list[Entry], system: SystemPromptPart | None, notes: list[str]) -> int:
        """The next request's estimated size with these entries, built as `before_model_request` builds it, with
        the shared estimator."""
        assert self.request_ctx is not None
        ctx, params = self.request_ctx
        parts: list[Any] = [system] if system is not None else []

        def tokens(status: str) -> int:
            request: list[ModelMessage] = [
                ModelRequest(parts=[*parts, UserPromptPart(self.render_with(entries, status))])
            ]
            return self.estimator.measure(ctx.model, request, params)[1]

        estimate = tokens(self.status(0, notes))
        notices = list(notes)
        if estimate >= PRESSURE_RATIO * self.budget_tokens:
            notices.append(self.pressure(estimate))
        return tokens(self.status(estimate, notices))

    def request_chars(self, entries: list[Entry], system: SystemPromptPart | None, status: str) -> int:
        assert self.request_ctx is not None
        ctx, params = self.request_ctx
        parts: list[Any] = [system] if system is not None else []
        request: list[ModelMessage] = [ModelRequest(parts=[*parts, UserPromptPart(self.render_with(entries, status))])]
        return self.estimator.measure(ctx.model, request, params)[0]

    def measure_entries(self, entries: list[Entry], system: SystemPromptPart | None, notes: list[str]) -> int:
        """The guard's projection: the shared estimate without this step's new entries, plus a conservative
        estimate of what they add, ceil(characters / r x 1.10)."""
        step = f"s{self.session.step}."
        base = [e for e in entries if not e.id.startswith(step)]
        shared_base = self.shared_measure(base, system, notes)
        r = self.density().r
        status = self.status(shared_base, notes)
        added = self.request_chars(entries, system, status) - self.request_chars(base, system, status)
        conservative = math.ceil(max(added, 0) / r * MARGIN)
        projected = shared_base + conservative
        if projected >= PRESSURE_RATIO * self.budget_tokens > shared_base:  # the PRESSURE line would be added too
            with_pressure = self.status(shared_base, [*notes, self.pressure(projected)])
            added = self.request_chars(entries, system, with_pressure) - self.request_chars(base, system, status)
            conservative = math.ceil(max(added, 0) / r * MARGIN)
            projected = shared_base + conservative
        self.last_check = {
            "r": round(r, 4),
            "shared_estimate": self.shared_measure(entries, system, notes),
            "conservative_estimate": projected,
        }
        self.record(
            {
                "type": "lite_guard_check",
                "step": self.session.step,
                "r": round(r, 4),
                "shared_base": shared_base,
                "added_chars": added,
                "conservative_added": conservative,
                "projected": projected,
            }
        )
        return projected

    # ------------------------------------------------------------------ the hooks

    async def before_model_request(
        self, ctx: RunContext[Any], request_context: "ModelRequestContext"
    ) -> "ModelRequestContext":
        params = request_context.model_request_parameters
        self.request_ctx = (ctx, params)
        try:
            self.rebuild(ctx, request_context)
        finally:
            self.request_ctx = None
        lines = tuple(render_entries(list(self.session.store.entries)).split("\n"))
        self.density().sent = (self.estimator.last_chars or 0, lines, self.task_text())
        return request_context

    def rebuild(self, ctx: RunContext[Any], request_context: "ModelRequestContext") -> None:
        """Replace the request with the rebuilt one, with PRESSURE and OVER LIMIT notices, or end the run."""
        params = request_context.model_request_parameters
        system, notes = self.absorb(list(request_context.messages))
        session = self.session

        def build(notices: list[str]) -> tuple[list[ModelMessage], int, int, bool]:
            parts: list[Any] = [system] if system is not None else []
            draft: list[ModelMessage] = [
                ModelRequest(parts=[*parts, UserPromptPart(self.render(self.status(0, notices)))])
            ]
            _, estimate, _ = self.estimator.measure(ctx.model, draft, params)
            request: list[ModelMessage] = [
                ModelRequest(parts=[*parts, UserPromptPart(self.render(self.status(estimate, notices)))])
            ]
            chars, tokens, appended = self.estimator.measure(ctx.model, request, params)
            return request, chars, tokens, appended

        request, chars, tokens, appended = build(notes)
        notices = list(notes)
        if tokens >= PRESSURE_RATIO * self.budget_tokens:
            notices.append(self.pressure(tokens))
        if tokens > self.limit:
            if session.over_limit or tokens > self.budget_tokens:
                self.record(
                    {
                        "type": "context_limit",
                        "source": "agent",
                        "estimated_tokens": tokens,
                        "limit_tokens": self.limit,
                        "reason": "still over the limit after one over-limit request"
                        if session.over_limit
                        else "over the budget itself",
                    }
                )
                raise ContextLimitError(f"the request (about {tokens} tokens) is over the {self.limit}-token limit")
            session.over_limit = True
            notices.append(
                f"OVER LIMIT: this request uses the {RESERVE_TOKENS:,}-token reserve. Reduce the working context in "
                "this step; if the next request is still over the limit, the run ends."
            )
            self.record({"type": "lite_over_limit", "estimated_tokens": tokens, "limit_tokens": self.limit})
        else:
            session.over_limit = False
        if notices != notes:
            request, chars, tokens, appended = build(notices)
        request_context.messages = request
        self.estimator.begin(chars, appended)

    async def after_model_request(
        self,
        ctx: RunContext[Any],
        *,
        request_context: "ModelRequestContext",
        response: ModelResponse,
    ) -> ModelResponse:
        del request_context  # required by the hook signature
        session = self.session
        # Observe r: the new text's characters per reported token, when this request extended the last answered one.
        density = self.density()
        sent, before = density.sent, density.answered
        tokens = response.usage.input_tokens
        extended = False  # for strict ratio learning (decisions P6)
        if sent is not None and tokens:
            if before is not None:
                chars, lines, task = sent
                old_chars, old_lines, old_task, old_tokens = before
                extends = task == old_task and lines[: len(old_lines)] == old_lines
                if extends and chars > old_chars and tokens > old_tokens:
                    density.lowest = min(density.lowest, (chars - old_chars) / (tokens - old_tokens))
                # The same test, except that an empty working context (rendered as one empty line) is a prefix of
                # any other.
                extended = extends or (task == old_task and old_lines == ("",))
            density.answered = (*sent, tokens)
        session.calls_this_run += 1
        self.estimator.record(
            ctx.model, response.usage.input_tokens, strict=self.strict_ratio_learning, extends=extended
        )
        calls = [p for p in response.parts if isinstance(p, ToolCallPart)]
        if len(calls) > 1:
            self.record({"type": "lite_parallel_calls", "calls": [c.tool_name for c in calls[1:]]})
        record = reported_overshoot(response.usage.input_tokens, self.budget_tokens, "agent")
        if self.end_over_budget and record is not None:
            self.record(record)
            raise BudgetExceededError(
                f"the call's reported input ({response.usage.input_tokens} tokens) was over the "
                f"{self.budget_tokens}-token budget"
            )
        state = self.guard()
        state.before, state.after_edit = session.store.entries, {}  # before this response's tools run
        return response
