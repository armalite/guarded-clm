"""The request layouts and the context cache point, offline: two turns, with a scripted model.

    uv run python examples/segmented_layout.py             # clm_context(): the default, segmented_task_last + 5m
    uv run python examples/segmented_layout.py segmented   # request_layout="segmented" (its marker is opt-in)
    uv run python examples/segmented_layout.py single      # request_layout="single": the original one-block layout

By default (decisions P13) each rebuilt request is a list of content items: the `<working_context>` opening, one item
per entry, a five-minute Pydantic AI `CachePoint`, then the `<task>` section and the status. A provider with prompt
caching can reuse the unchanged working context within a turn and across turns; only what follows the marker
changes. A new turn appends the previous answer as an entry. The model sees the task after its working context.

- `"segmented"` (decisions P8) keeps the `<task>` section first, so a new turn changes it and the cached context is
  reused only within a turn. Pass `cache_point="5m"` for its marker.
- `"single"` sends the whole request as one text block, as the stored transcripts test it; nothing after the tools
  and system prompt can be reused.
- `cache_point=None` keeps a segmented layout but sends no marker.

No provider is called here; the script prints each request's items and where the cache point sits.

With a real Anthropic model, the recommended host model settings are `anthropic_cache_tool_definitions` and
`anthropic_cache_instructions` (for example both `"5m"`), without `anthropic_cache` or `anthropic_cache_messages`:
those mark the last block, the status tail, which changes on every request (decisions P11). With the library's
marker that makes three breakpoints. The marker's TTL must not be longer than those before it.
"""

import asyncio
import sys
from collections.abc import Iterator
from typing import Literal, cast

from pydantic_ai import Agent, UsageLimits
from pydantic_ai.messages import (
    CachePoint,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from guarded_clm import CALL_CAP, clm_context

Layout = Literal["single", "segmented", "segmented_task_last"]


def service_status(service: str) -> str:
    """Read one service's status (synthetic data)."""
    return f"{service}: degraded since 09:12 UTC, error rate 4.1%, last deploy 08:55 UTC, owner team-blue."


def scripted_responses() -> Iterator[ModelResponse]:
    yield ModelResponse(parts=[ToolCallPart("service_status", {"service": "checkout-api"}, tool_call_id="c1")])
    yield ModelResponse(parts=[ToolCallPart("service_status", {"service": "billing-api"}, tool_call_id="c2")])
    yield ModelResponse(parts=[TextPart("checkout-api and billing-api have both been degraded since 09:12 UTC.")])
    yield ModelResponse(parts=[ToolCallPart("service_status", {"service": "search-api"}, tool_call_id="c3")])
    yield ModelResponse(parts=[TextPart("search-api is degraded too, with the same owner.")])


def show(messages: list[ModelMessage]) -> None:
    part = next(p for m in messages if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, UserPromptPart))
    items = part.content if isinstance(part.content, list) else [part.content]
    print(f"request with {len(items)} items:")
    for item in items:
        if isinstance(item, CachePoint):
            print(f"    [cache point, ttl {item.ttl}]")
        elif str(item).startswith("{"):  # one entry's JSON line
            print(f"    {str(item).strip()[:96]}")
        else:  # fixed text: show its section tags
            print("    " + " ".join(line for line in str(item).split("\n") if line.startswith("<")))


def scripted_model() -> FunctionModel:
    responses = scripted_responses()

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del info
        show(messages)
        return next(responses)

    return FunctionModel(respond)


async def main(layout: Layout | None) -> None:
    agent = Agent(
        scripted_model(),
        system_prompt="You are an on-call assistant. Use the tools to answer.",
        tools=[service_status],
        capabilities=[clm_context() if layout is None else clm_context(request_layout=layout)],
    )
    history: list[ModelMessage] | None = None
    for prompt in ("Which services are degraded?", "Is search-api affected as well?"):
        print(f"--- user: {prompt}")
        result = await agent.run(
            prompt,
            message_history=history,
            conversation_id="example-conversation",
            usage_limits=UsageLimits(request_limit=CALL_CAP),
        )
        history = result.all_messages()
        print(f"agent: {result.output}")


if __name__ == "__main__":
    chosen = sys.argv[1] if len(sys.argv) > 1 else None
    if chosen not in (None, "single", "segmented", "segmented_task_last"):
        sys.exit("usage: segmented_layout.py [single|segmented|segmented_task_last]")
    asyncio.run(main(cast(Layout | None, chosen)))
