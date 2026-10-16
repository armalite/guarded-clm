"""Request size estimates, anchored on the provider's reported input tokens.

`Calibration` and `ConversationStore`; `SizeEstimator`, the reserve and the reported-overshoot rule; and
`request_chars` and `part_text`, which measure a request.
"""

import dataclasses
import json
from collections import OrderedDict
from collections.abc import Callable, Sequence
from typing import Any, Generic, Literal, TypeVar

from pydantic_ai.messages import InstructionPart, ModelMessage, ModelMessagesTypeAdapter, ModelRequest, UserPromptPart
from pydantic_ai.models import Model, ModelRequestParameters

# Starting characters-per-token, before any provider usage has been seen. Also the fixed ratio for scripted models.
INITIAL_CHARS_PER_TOKEN = 4.0
# Starting characters-per-token for text added since the last call; it only ever moves down.
INITIAL_NEW_TEXT_CHARS_PER_TOKEN = 2.5
MIN_NEW_TEXT_TOKENS = 200
# A new-text observation below this many characters per token is ignored as implausible, so it never lowers the
# new-text ratio (decisions P7, SPEC 13.16).
MIN_PLAUSIBLE_NEW_TEXT_CHARS_PER_TOKEN = 0.5
# Scripted models report made-up usage, so their usage is never used to calibrate.
SCRIPTED_MODEL_SYSTEMS = frozenset({"function", "test"})
MAX_CONVERSATIONS = 512
RESERVE_TOKENS = 2_048  # the response reserve: the enforced limit is the budget minus this

EstimateMethod = Literal["anchored", "calibrated", "fixed_ratio"]


def enforced_limit(budget_tokens: int) -> int:
    return budget_tokens - RESERVE_TOKENS


def reports_real_usage(model: Model) -> bool:
    """False for FunctionModel and TestModel, whose usage is a word-count guess, not provider figures."""
    return model.system not in SCRIPTED_MODEL_SYSTEMS


def reported_overshoot(input_tokens: int, budget_tokens: int, source: str) -> dict[str, Any] | None:
    """The `context_limit` record for a call whose reported input was over the budget, or None."""
    if input_tokens <= budget_tokens:
        return None
    return {
        "type": "context_limit",
        "source": source,
        "reported_input_tokens": input_tokens,
        "budget_tokens": budget_tokens,
    }


def part_text(part: UserPromptPart) -> str:
    """Text of a user prompt part. Non-text content (images, files) is ignored."""
    if isinstance(part.content, str):
        return part.content
    return "".join(item for item in part.content if isinstance(item, str))


def request_chars(messages: Sequence[ModelMessage], params: ModelRequestParameters | None = None) -> int:
    """Characters in what a request sends: messages, the instructions actually sent, and tool definitions.

    Messages are serialized without their `instructions`, because only the current request's instructions are sent.
    Divide by a calibrated characters-per-token figure to estimate tokens.
    """
    stripped = [dataclasses.replace(m, instructions=None) if isinstance(m, ModelRequest) else m for m in messages]
    chars = len(ModelMessagesTypeAdapter.dump_json(stripped))
    if params is not None and params.instruction_parts is not None:
        instructions = InstructionPart.join(params.instruction_parts)
    else:
        instructions = next((m.instructions for m in reversed(messages) if isinstance(m, ModelRequest)), None)
    chars += len(instructions or "")
    if params is not None:
        tools = [
            {"name": t.name, "description": t.description, "parameters": t.parameters_json_schema}
            for t in [*params.function_tools, *params.output_tools]
        ]
        chars += len(json.dumps(tools)) if tools else 0
    return chars


@dataclasses.dataclass
class Calibration:
    """Token calibration for one conversation. It persists across the runs (turns) of that conversation.

    `anchored`: the previous call's reported input plus the characters added since, at the new-text ratio.
    `calibrated`: all characters at the overall ratio, used after the context shrank. On the first call of a
    conversation, before any usage has been seen, all characters count at the new-text ratio instead.
    `fixed_ratio`: all characters / 4, with no calibration. Used for scripted models, so offline runs are deterministic.

    Two ratios, because new text is often denser than the rest: a call that adds 50k characters of log output can
    run at 2 characters per token while the instructions and earlier turns run at 3 to 4. The overall ratio moves
    halfway towards each call's observed figure. The new-text ratio starts at a conservative 2.5 and keeps the lowest
    value seen, so the estimate errs early (more tokens) rather than late.

    A request counts as appended when it is no smaller than the last one, so a step that removed about as much as it
    added can produce an observation far below any real text density. An observation below 0.5 characters per token
    is ignored (decisions P7, SPEC 13.16).
    """

    chars_per_token: float = INITIAL_CHARS_PER_TOKEN
    new_text_chars_per_token: float = INITIAL_NEW_TEXT_CHARS_PER_TOKEN
    last_request_chars: int | None = None
    last_reported_input: int | None = None
    pending_chars: int | None = None
    pending_appended: bool = False

    def estimate(self, chars: int, *, real_usage: bool, appended: bool) -> tuple[int, EstimateMethod]:
        """Estimate a request of `chars` characters. `appended`: nothing was removed since the last request."""
        if not real_usage:
            return round(chars / INITIAL_CHARS_PER_TOKEN), "fixed_ratio"
        last_chars, last_input = self.last_request_chars, self.last_reported_input
        if appended and last_chars is not None and last_input is not None and chars >= last_chars:
            return last_input + round((chars - last_chars) / self.new_text_chars_per_token), "anchored"
        if last_input is None:
            # No calibration yet in this conversation: all of it is new text, so use the conservative ratio.
            return round(chars / self.new_text_chars_per_token), "calibrated"
        return round(chars / self.chars_per_token), "calibrated"

    def begin_request(self, chars: int, *, appended: bool) -> None:
        """Remember the request being sent, so its reported usage can calibrate both ratios."""
        self.pending_chars = chars
        self.pending_appended = appended

    def record_usage(self, reported_input: int, *, real_usage: bool) -> None:
        """After a call: update both ratios from the reported input, and set the anchor."""
        chars, appended = self.pending_chars, self.pending_appended
        self.pending_chars, self.pending_appended = None, False
        if not real_usage or not chars or reported_input <= 0:
            return
        last_chars, last_input = self.last_request_chars, self.last_reported_input
        if appended and last_chars is not None and last_input is not None:
            added_chars, added_tokens = chars - last_chars, reported_input - last_input
            # Small additions are too noisy to calibrate on.
            if added_chars > 0 and added_tokens >= MIN_NEW_TEXT_TOKENS:
                observed = added_chars / added_tokens
                if observed >= MIN_PLAUSIBLE_NEW_TEXT_CHARS_PER_TOKEN:
                    self.new_text_chars_per_token = round(min(self.new_text_chars_per_token, observed), 4)
        self.chars_per_token = round(0.5 * self.chars_per_token + 0.5 * chars / reported_input, 4)
        self.last_request_chars = chars
        self.last_reported_input = reported_input


T = TypeVar("T")


class ConversationStore(Generic[T]):
    """Per-conversation state by conversation id, in memory, keeping the most recently used conversations."""

    def __init__(self, factory: Callable[[], T], max_conversations: int = MAX_CONVERSATIONS) -> None:
        self.factory = factory
        self.max_conversations = max_conversations
        self._by_conversation: OrderedDict[str, T] = OrderedDict()

    def get(self, conversation_id: str) -> T:
        state = self._by_conversation.pop(conversation_id, None)
        if state is None:
            state = self.factory()
        self._by_conversation[conversation_id] = state
        while len(self._by_conversation) > self.max_conversations:
            self._by_conversation.popitem(last=False)
        return state


@dataclasses.dataclass
class SizeEstimator:
    """Per-conversation request sizes: provider-reported input of the last call, plus the text added since."""

    calibration: Calibration = dataclasses.field(default_factory=Calibration)
    last_chars: int | None = None

    def measure(
        self, model: Model, messages: list[ModelMessage], params: ModelRequestParameters | None
    ) -> tuple[int, int, bool]:
        """(characters, estimated tokens, appended) for a request, without recording it."""
        chars = request_chars(messages, params)
        appended = self.last_chars is None or chars >= self.last_chars
        tokens, _ = self.calibration.estimate(chars, real_usage=reports_real_usage(model), appended=appended)
        return chars, tokens, appended

    def begin(self, chars: int, appended: bool) -> None:
        self.calibration.begin_request(chars, appended=appended)
        self.last_chars = chars

    def record(self, model: Model, input_tokens: int) -> None:
        self.calibration.record_usage(input_tokens, real_usage=reports_real_usage(model))
