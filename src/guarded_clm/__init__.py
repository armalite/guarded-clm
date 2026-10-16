"""ClmContext: the model manages its own working context within a token budget, for Pydantic AI agents.

Add it to an agent with `clm_context()`:

    from pydantic_ai import Agent, UsageLimits
    from guarded_clm import CALL_CAP, clm_context

    agent = Agent(model, capabilities=[clm_context()])
    result = await agent.run(prompt, conversation_id=conversation, usage_limits=UsageLimits(request_limit=CALL_CAP))

See README.md for when to use it, and SPEC.md for exactly what it does.
"""

from collections.abc import Callable
from typing import Any

from pydantic_ai.capabilities import CombinedCapability

from guarded_clm.capability import ClmContext
from guarded_clm.context import ContextLimits
from guarded_clm.errors import BudgetExceededError, ContextLimitError, StepLimitError
from guarded_clm.estimate import RESERVE_TOKENS
from guarded_clm.steps import ClmStepLimit, lm_call_cap

__version__ = "0.2.0.dev0"

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
    strict_ratio_learning: bool = False,
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
        strict_ratio_learning: The shared estimator learns its new-text ratio only from requests that extend the
            last answered one, and ignores implausible observations (decisions P6). Off by default.
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
                strict_ratio_learning=strict_ratio_learning,
            ),
        ]
    )


__all__ = [
    "BUDGET_TOKENS",
    "CALL_CAP",
    "MAX_STEPS",
    "RESERVE_TOKENS",
    "BudgetExceededError",
    "ClmContext",
    "ClmStepLimit",
    "ContextLimitError",
    "ContextLimits",
    "StepLimitError",
    "__version__",
    "clm_context",
]
