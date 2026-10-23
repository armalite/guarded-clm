"""ClmContext: the model manages its own working context within a token budget, for Pydantic AI agents.

Add it to an agent with `clm_context()`:

    from pydantic_ai import Agent, UsageLimits
    from guarded_clm import CALL_CAP, clm_context

    agent = Agent(model, capabilities=[clm_context()])
    result = await agent.run(prompt, conversation_id=conversation, usage_limits=UsageLimits(request_limit=CALL_CAP))

By default each request puts the working context first, with a five-minute prompt-cache marker after it, so a
provider with prompt caching can reuse it within and across turns. See README.md for when to use it and the
recommended host settings, and SPEC.md for exactly what it does.
"""

from collections.abc import Callable
from typing import Any, Literal

from pydantic_ai.capabilities import CombinedCapability

from guarded_clm.capability import DEFAULT, REQUEST_LAYOUTS, ClmContext, DefaultCachePoint
from guarded_clm.context import ContextLimits
from guarded_clm.errors import BudgetExceededError, ContextLimitError, StepLimitError
from guarded_clm.estimate import RESERVE_TOKENS
from guarded_clm.steps import ClmStepLimit, add_context_note, lm_call_cap

__version__ = "0.3.0.dev0"

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
    request_layout: Literal["single", "segmented", "segmented_task_last"] = "segmented_task_last",
    cache_point: Literal["5m", "1h"] | DefaultCachePoint | None = DEFAULT,
) -> CombinedCapability[Any]:
    """ClmContext as tested: `ClmStepLimit` (free edit steps, the call cap, the finalize notice), then `ClmContext`.

    The defaults are the tested settings, with the cache-friendly request layout (decisions P13). Args:
        budget_tokens: The context budget; the enforced limit is 2,048 tokens less.
        max_steps: Task steps per run (user turn); edit steps are free.
        call_cap: Model calls per run, edit steps included. Default 2 x max_steps + 24 (144).
        end_over_budget: End the session when a call's reported input is over the budget.
        max_output_chars: The cap on the edit tool's result.
        limits: The working context's entry, body and total limits.
        on_record: Called with each trace record.
        id: ClmContext's capability id, if another capability already uses `clm_context`. ClmStepLimit's id is
            `clm_step_limit`.
        request_layout: `"segmented_task_last"` (the default since 0.3.0.dev0, decisions P13): one content item per
            working-context entry with the `<task>` section after them, so a provider can reuse the unchanged context
            within and across turns (decisions P9). `"single"`: the original one-block layout, as the stored
            transcripts test it. `"segmented"`: the single layout's text as items, reusable within a turn (P8).
        cache_point: A Pydantic AI `CachePoint` TTL (`"5m"` or `"1h"`) after the last entry, or None for no marker.
            Omitted, it is `"5m"` with `"segmented_task_last"` and no marker with the other layouts.
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
