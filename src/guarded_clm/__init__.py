"""ClmContext: the model manages its own working context within a token budget, for Pydantic AI agents.

Add it to an agent with `clm_context()`:

    from pydantic_ai import Agent, UsageLimits
    from guarded_clm import CALL_CAP, clm_context

    agent = Agent(model, capabilities=[clm_context()])
    result = await agent.run(prompt, conversation_id=conversation, usage_limits=UsageLimits(request_limit=CALL_CAP))

See README.md for when to use it, and SPEC.md for exactly what it does.
"""

from collections.abc import Callable
from typing import Any, Literal

from pydantic_ai.capabilities import CombinedCapability

from guarded_clm.capability import REQUEST_LAYOUTS, ClmContext
from guarded_clm.context import ContextLimits
from guarded_clm.errors import BudgetExceededError, ContextLimitError, StepLimitError
from guarded_clm.estimate import RESERVE_TOKENS
from guarded_clm.steps import ClmStepLimit, add_context_note, lm_call_cap

__version__ = "0.2.0.dev2"

BUDGET_TOKENS = 32_000
MAX_STEPS = 60
CALL_CAP = lm_call_cap(MAX_STEPS)  # 144


def clm_context(
    *,
    budget_tokens: int = BUDGET_TOKENS,
    max_steps: int = MAX_STEPS,
    call_cap: int | None = None,
    end_over_budget: bool = False,
    max_output_chars: int = 8_000,
    limits: ContextLimits | None = None,
    on_record: Callable[[dict[str, Any]], None] | None = None,
    id: str = "clm_context",
    request_layout: Literal["single", "segmented", "segmented_task_last"] = "single",
    cache_point: Literal["5m", "1h"] | None = None,
) -> CombinedCapability[Any]:
    """ClmContext as tested: `ClmStepLimit` (free edit steps, the call cap, the finalize notice), then `ClmContext`.

    The defaults are the tested settings. Args:
        budget_tokens: The context budget; the enforced limit is 2,048 tokens less.
        max_steps: Task steps per run (user turn); edit steps are free.
        call_cap: Model calls per run, edit steps included. Default 2 x max_steps + 24 (144).
        end_over_budget: End the session when a call's reported input is over the budget.
        max_output_chars: The cap on the edit tool's result.
        limits: The working context's entry, body and total limits.
        on_record: Called with each trace record.
        id: ClmContext's capability id, if another capability already uses `clm_context`. ClmStepLimit's id is
            `clm_step_limit`.
        request_layout: `"single"` (the default, as tested); `"segmented"`: the same request text as several
            content items, one per working-context entry, so a provider can reuse the unchanged context within a
            turn (decisions P8); or `"segmented_task_last"`: the same items with the `<task>` section after the
            working context, so the context stays reusable across turns too (decisions P9).
        cache_point: With a segmented layout, a Pydantic AI `CachePoint` TTL (`"5m"` or `"1h"`) placed after the
            last entry, or None for no marker.
    """
    cap = call_cap if call_cap is not None else lm_call_cap(max_steps)
    return CombinedCapability(
        capabilities=[
            ClmStepLimit(max_steps, call_cap=cap),
            ClmContext(
                budget_tokens=budget_tokens,
                end_over_budget=end_over_budget,
                max_output_chars=max_output_chars,
                max_steps=max_steps,
                call_cap=cap,
                limits=limits,
                on_record=on_record,
                id=id,
                request_layout=request_layout,
                cache_point=cache_point,
            ),
        ]
    )


__all__ = [
    "BUDGET_TOKENS",
    "CALL_CAP",
    "MAX_STEPS",
    "REQUEST_LAYOUTS",
    "RESERVE_TOKENS",
    "BudgetExceededError",
    "ClmContext",
    "ClmStepLimit",
    "ContextLimitError",
    "ContextLimits",
    "StepLimitError",
    "__version__",
    "add_context_note",
    "clm_context",
]
