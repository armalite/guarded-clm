# guarded-clm
Model-controlled working context for AI agents, with structured edits, guarded tool outputs and protected conversation history.

**Status:** 0.1.0.dev0, unreleased. `ClmContext` is a [Pydantic AI](https://ai.pydantic.dev/) capability. With it, the agent's model manages its own **working context** within a fixed token budget, and the runtime checks every edit and keeps each request within the limit.

**What this repository's verification covers:**
- **Offline only,** with scripted models;
- **Fixed behaviour:** stored transcripts pin it exactly (see [docs/results.md](docs/results.md)).

**What it does not show:** this repository makes no claim about task quality, cost or speed with any real model.

**This is context management, not a security guardrail.** It doesn't filter content, detect prompt injection or sandbox code. The runtime checks apply to the working context only.

## How it works

The model doesn't receive a growing message history. Every request is rebuilt from a list of entries: its actions, the tools' outputs, receipts and its own notes. The request has three parts:

- **`<task>`:** the conversation's first request, plus the current one.
- **`<working_context>`:** the entries, one JSON object per line.
- **`<runtime_status>`:** the step, the calls, the size against the limit, and any notices.

The model tidies the context with one tool, `edit_context`. With it, the model can delete entries, replace an entry's text, or add a note, and it decides what to keep. The runtime:

- **checks every edit** and applies it all or nothing;
- **protects earlier turns:** earlier requests and the answers given to them can't be edited;
- **holds back tool outputs** that would push the next request over the limit. It uses a conservative size estimate and asks the model to tidy, then call again;
- **reports the size** in every request, with a PRESSURE notice from 70% of the budget;
- **allows one request into a 2,048-token reserve,** and ends the run if the next one is still over the limit;
- **doesn't count edit steps** against the task-step limit.

No model-written code runs: edits are structured operations applied by the runtime.

## Install

Not yet published. From a checkout:

```bash
uv sync
```

The only runtime dependency is `pydantic-ai-slim>=2.27.1,<2.28`, the Pydantic AI version the design was tested on. Python 3.11 to 3.13.

## Usage

```python
from pydantic_ai import Agent, UsageLimits
from guarded_clm import CALL_CAP, clm_context

agent = Agent(model, system_prompt=SYSTEM, tools=[...], capabilities=[clm_context()])

result = await agent.run(
    user_message,
    conversation_id=conversation_id,  # the working context is kept per conversation
    message_history=history,  # the previous turn's messages: its answer becomes a protected entry
    usage_limits=UsageLimits(request_limit=CALL_CAP),  # Pydantic AI's default limit (50) is lower than 144
)
```

**Pass both `conversation_id` and `message_history` on every turn.** With the id alone, the working context continues, but the previous turn's answer is never added to it ([SPEC.md](SPEC.md), section 12).

A runnable, offline version with a scripted model and two turns is in [examples/basic_agent.py](examples/basic_agent.py):

```bash
uv run python examples/basic_agent.py
```

`clm_context()` takes these settings:

| Setting | Default | Meaning |
|---|---|---|
| `budget_tokens` | 32,000 | The context budget. The enforced limit is 2,048 less. |
| `max_steps` | 60 | Task steps per run (user turn); edit steps don't count. |
| `call_cap` | 144 | Model calls per run, edit steps included (2 × `max_steps` + 24). |
| `end_over_budget` | `False` | End the session if a call's reported input is over the budget. |
| `max_output_chars` | 8,000 | Cap on the edit tool's result. |
| `limits` | 200 entries, 24,000 characters per entry, 120,000 in total, 256,000 bytes serialized | The working context's limits. |
| `on_record` | `None` | Called with each trace record (a dict), for logging or tracing. |
| `id` | `"clm_context"` | The capability id. Pass another if a different capability already uses `clm_context`. |

**How a run can end early:**

- `ContextLimitError`: over the limit twice in a row, or over the budget;
- `BudgetExceededError`: only with `end_over_budget`;
- `StepLimitError`;
- Pydantic AI's `UsageLimitExceeded`: the call cap.

## Before you use it

- **Use read-only tools.** The guard runs a tool, then may hold back its output and ask the model to call it again. A tool that sends, writes or pays would run twice.
- **Cap your tools' outputs** (the defaults assume about 8,000 characters).
  - An entry over 24,000 characters blocks every edit until it is deleted.
  - An output too big to fit even in a tidied context is held back every time. The model may then tidy and retry until a cap ends the run.
- **Expect protected history to grow.** Every earlier request and answer stays in the context, protected, and nothing summarises or evicts old turns. Very long conversations use a growing share of the budget.
- **Keep a conversation on one process.** The working context lives in memory in the capability. Every turn of a conversation needs the same agent object, so use sticky routing if you run several replicas.
- **Keep your own chat transcript.** After a turn, `result.all_messages()` holds only the last rebuilt request and its reply, not a readable transcript.
- **Know the documented quirks:**
  - two edits in one response race;
  - the task-step count shown to the model can drift from the enforced one;
  - some malformed edits and any raising tool end the run;
  - images aren't shown to the model.

The full list is in [SPEC.md, sections 13 and 14](SPEC.md#13-known-properties-and-limits).

## Documentation

- [SPEC.md](SPEC.md): the exact behaviour (normative).
- [docs/architecture.md](docs/architecture.md): the structure.
- [docs/decisions.md](docs/decisions.md): decisions and open questions.
- [docs/results.md](docs/results.md): offline verification and its limits.
- [docs/implementation-plan.md](docs/implementation-plan.md): the roadmap.

## Develop

```bash
make install
make check              # lint, format, types (pyright) and tests, all offline
make transcripts-check  # compare the library with the stored transcripts
```

Any behaviour change needs a decision and new transcripts. See [AGENTS.md](AGENTS.md).

## Licence

Apache-2.0 ([LICENSE](LICENSE)). Parts derive from [clm-lib](https://github.com/armalite/clm-lib), also Apache-2.0 (see [NOTICE](NOTICE)). The design follows the Context Language Models paper (arXiv 2609.37725). No code, prompt or notice text is taken from the paper's CC BY-NC reference repository.
