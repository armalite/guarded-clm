"""`ClmStepLimit`: the task-step limit with free edit steps, the model-call cap, and the finalize notice.

It runs in front of `ClmContext`:

- **Task steps:** every model response is a task step, except one whose first tool call is `edit_context`. At
  `max_steps` task steps the next request raises `StepLimitError`.
- **Model calls:** every response counts, edit steps included. At `call_cap` calls the next request raises
  `UsageLimitExceeded`.
- **The finalize notice:** when at most 3 steps or calls are left, the request gets a
  "[context manager] N step(s) left: finish now and give your final answer." prompt part. ClmContext shows it in
  the runtime status.

Both counts are per run (one user turn). It must come before `ClmContext` in the capability list, as tested:
`clm_context()` builds them in that order.
"""

from typing import TYPE_CHECKING, Any

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.tools import RunContext

from guarded_clm.errors import StepLimitError

if TYPE_CHECKING:
    from pydantic_ai.capabilities import ModelRequestContext

EDIT_TOOL = "edit_context"
NOTE = "[context manager]"
FINALIZE_STEPS = 3


def lm_call_cap(max_steps: int) -> int:
    """The default model-call cap: every call counts, free edit steps included."""
    return 2 * max_steps + 24


class ClmStepLimit(AbstractCapability[Any]):
    """The task-step limit per run, where edit steps are free, the model-call cap, and the finalize notice."""

    def __init__(self, max_steps: int = 60, *, call_cap: int | None = None, finalize: bool = True) -> None:
        self.id = "clm_step_limit"
        self.description = "The task-step limit (edit steps are free) and the model-call cap."
        self.defer_loading = False
        self.max_steps = max_steps
        self.call_cap = call_cap if call_cap is not None else lm_call_cap(max_steps)
        self.finalize = finalize
        self.steps = 0
        self.calls = 0

    async def for_run(self, ctx: RunContext[Any]) -> "ClmStepLimit":
        del ctx  # fresh counts per run
        return ClmStepLimit(self.max_steps, call_cap=self.call_cap, finalize=self.finalize)

    async def before_model_request(
        self, ctx: RunContext[Any], request_context: "ModelRequestContext"
    ) -> "ModelRequestContext":
        del ctx
        if self.steps >= self.max_steps:
            raise StepLimitError(f"the task-step limit of {self.max_steps} was reached")
        if self.calls >= self.call_cap:
            raise UsageLimitExceeded(f"the LM-call cap of {self.call_cap} was reached")
        left = min(self.max_steps - self.steps, self.call_cap - self.calls)
        final = request_context.messages[-1]
        if self.finalize and left <= FINALIZE_STEPS and isinstance(final, ModelRequest):
            final.parts = [
                *final.parts,
                UserPromptPart(f"{NOTE} {left} step(s) left: finish now and give your final answer."),
            ]
        return request_context

    async def after_model_request(
        self,
        ctx: RunContext[Any],
        *,
        request_context: "ModelRequestContext",
        response: ModelResponse,
    ) -> ModelResponse:
        del ctx, request_context  # required by the hook signature
        self.calls += 1
        calls = [p for p in response.parts if isinstance(p, ToolCallPart)]
        if not (calls and calls[0].tool_name == EDIT_TOOL):
            self.steps += 1
        return response
