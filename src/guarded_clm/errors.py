"""How a ClmContext run can end early. Each is a Pydantic AI `AgentRunError`, raised out of `agent.run`."""

from pydantic_ai.exceptions import AgentRunError


class ContextLimitError(AgentRunError):
    """A request would exceed the context limit, so the run ends.

    Raised when a request is over the limit (the budget minus the reserve) right after one that already used the
    reserve, or when a request is estimated over the budget itself.
    """


class BudgetExceededError(ContextLimitError):
    """A call's provider-reported input was over the budget itself, so the session ends.

    Only with `end_over_budget=True`. The limit is checked on estimates before each call, with the 2,048-token
    reserve as the margin for their error: a reported input inside the reserve does not end the session.
    """


class StepLimitError(AgentRunError):
    """The task-step limit was reached (edit steps don't count towards it)."""
