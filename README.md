# guarded-clm
Model-controlled working context for AI agents, with structured edits, guarded tool outputs and protected conversation history.

> **Status: early development (0.1.0.dev0), offline only.** The core context store, transactional edits and protected turn records are implemented and tested. The request builder, size estimation, output guard, recovery behaviour and provider adapters are **planned, not implemented**. Nothing has been released, and the package name's availability on public indexes is unverified.

## What it is

guarded-clm keeps an agent's **working context** as an authoritative, structured document that the runtime rebuilds every request from. The principle:

> The model manages editable working context. The runtime validates edits, protects designated conversation records and enforces the configured budget.

**How it behaves:**
- **The model edits through structured, all-or-nothing transactions:** delete entries, replace a body, add a note. Each transaction is validated in full before it is committed. Rejections are explicit, and receipts list IDs, never removed text.
- **Earlier user requests and final answers become protected records.** Protection is runtime-owned, and models cannot forge it or occupy the runtime's reserved IDs.

**This is context management, not a security guardrail.** It does not detect prompt injection, filter content or sandbox code. Its host APIs validate model input but are not a security boundary.

## Example

```python
from guarded_clm import GuardedConfig, GuardedContext

ctx = GuardedContext(GuardedConfig(budget_tokens=32_000))

ctx.start_turn("Investigate why the nightly export failed.")  # turn 1: the task anchor
state = ctx.prepare_request().state  # hook for the request builder
r = ctx.apply_edits(
    [{"op": "add_note", "id": "findings", "body": "Exit code 3: quota exceeded."}],
    expected_revision=state.snapshot.revision,
)
print(r.outcome, r.receipt())  # accepted {... 'added': ['findings'] ...}
ctx.submit_answer("Raise the quota and add a free-space check.")

ctx.start_turn("Were last week's exports complete?")  # turn 2
prep = ctx.prepare_request()  # materialises t1.answer (protected)
r = ctx.apply_edits(
    [{"op": "delete", "ids": ["t1.answer"]}], expected_revision=prep.state.snapshot.revision
)
print(r.outcome, r.reason)  # rejected delete: 't1.answer' is protected
```

The full, runnable version is [`examples/basic_turns.py`](examples/basic_turns.py):

```bash
uv sync
uv run python examples/basic_turns.py
```

## Implemented in 0.1

- validated configuration (`GuardedConfig`);
- entries with ID, role and body rules, and structural limits (200 entries; 24,000 code points per body; 120,000 code points in total; 256,000 UTF-8 bytes of the canonical serialisation);
- the authoritative `ContextStore`, with revisions and immutable snapshots;
- transactional `delete` / `replace` / `add_note`;
- reserved runtime namespaces;
- the turn lifecycle (`GuardedContext`): the task anchor, the host-owned current request, protected earlier requests and answers, and an idempotent `prepare_request()` hook.

## Planned

- the request builder;
- conservative size estimation anchored to reported usage;
- an output guard for tool results;
- pressure and recovery behaviour;
- in-order processing of several tool calls per response;
- an Anthropic adapter and a Pydantic AI capability.

See [docs/implementation-plan.md](docs/implementation-plan.md).

## Documentation

- [SPEC.md](SPEC.md): behaviour, both implemented and planned;
- [docs/decisions.md](docs/decisions.md): decisions and open questions;
- [docs/architecture.md](docs/architecture.md): structure;
- [docs/results.md](docs/results.md): offline verification. There are no performance claims.

## Development

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest
```

## License

Apache-2.0. See [LICENSE](LICENSE).
