"""ClmContext in a Pydantic AI agent, offline: two turns of one conversation, with a scripted model.

    uv run python examples/basic_agent.py

The scripted `FunctionModel` stands in for a real model: it reads a service's status, tidies its working context
with `edit_context`, answers, and then answers a follow-up. No provider is called. Each trace record's type is
printed as it arrives, and the last request the model saw is shown at the end.
"""

import asyncio
from collections.abc import Iterator
from typing import Any

from pydantic_ai import Agent, UsageLimits
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from guarded_clm import CALL_CAP, clm_context


def service_status(service: str) -> str:
    """Read one service's status (synthetic data)."""
    return f"{service}: degraded since 09:12 UTC, error rate 4.1%, last deploy 08:55 UTC, owner team-blue."


def scripted_responses() -> Iterator[ModelResponse]:
    # Turn 1: read the status, keep a note and drop the raw output, then answer.
    yield ModelResponse(parts=[ToolCallPart("service_status", {"service": "checkout-api"}, tool_call_id="c1")])
    yield ModelResponse(
        parts=[
            ToolCallPart(
                "edit_context",
                {
                    "ops": [
                        {
                            "op": "add_note",
                            "id": "n1",
                            "body": "checkout-api: degraded 09:12, 4.1% errors, 08:55 deploy",
                        },
                        {"op": "delete", "ids": ["s1.obs"]},
                    ]
                },
                tool_call_id="e1",
            )
        ]
    )
    yield ModelResponse(parts=[TextPart("checkout-api has been degraded since 09:12 UTC, 17 minutes after a deploy.")])
    # Turn 2: the note is still in the working context, so it answers straight away.
    yield ModelResponse(parts=[TextPart("The last deploy was at 08:55 UTC.")])


def scripted_model() -> FunctionModel:
    responses = scripted_responses()

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del messages, info  # a real model would read the rebuilt request
        return next(responses)

    return FunctionModel(respond)


def on_record(record: dict[str, Any]) -> None:
    print(f"  record: {record['type']}")


async def main() -> None:
    agent = Agent(
        scripted_model(),
        system_prompt="You are an on-call assistant. Use the tools to answer.",
        tools=[service_status],
        capabilities=[clm_context(on_record=on_record)],
    )
    history: list[ModelMessage] | None = None
    for prompt in ("What's wrong with checkout-api?", "When was its last deploy?"):
        print(f"user: {prompt}")
        result = await agent.run(
            prompt,
            conversation_id="example-conversation",  # the working context is kept per conversation
            # The previous turn's messages: its final answer becomes the protected entry t1.answer. Without them,
            # the working context still continues, but that answer is never added.
            message_history=history,
            usage_limits=UsageLimits(request_limit=CALL_CAP),  # Pydantic AI's default (50) is lower than 144
        )
        history = result.all_messages()
        print(f"agent: {result.output}\n")
    if history:
        text = next(p.content for p in history[0].parts if isinstance(p, UserPromptPart))
        print("The last request the model saw:\n" + (text if isinstance(text, str) else str(text)))


if __name__ == "__main__":
    asyncio.run(main())
