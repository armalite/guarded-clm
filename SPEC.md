# guarded-clm specification

Version 0.1 (15 October 2026). This document defines behaviour. Sections §1–§8 are **implemented**. §9 is **planned** and not implemented. The roadmap is in [docs/implementation-plan.md](docs/implementation-plan.md), decisions and open items in [docs/decisions.md](docs/decisions.md), and the structure in [docs/architecture.md](docs/architecture.md).

## Version history

- **0.1 (2026-10-15):** configuration, entries and limits, canonical serialisation, transactional edits, reserved namespaces, runtime-owned protection and the turn lifecycle (plan chunk 1). The planned design is recorded in §9.

## 1. Purpose and principle

guarded-clm manages an agent's **working context**: the editable part of what the model sees on each request.

> The model manages editable working context. The runtime validates edits, protects designated conversation records and enforces the configured budget.

The finished design rebuilds every request from an authoritative working context, with no hidden history replay. In that design:
- the model changes the context through structured edits;
- several tool calls in one response are processed in order;
- an output guard and conservative size estimation keep requests within budget;
- earlier user requests and final answers are protected;
- pressure and recovery behaviour are explicit.

**This is context management, not a security guardrail.** It does not detect prompt injection, filter content or sandbox code.

## 2. Configuration (implemented)

`GuardedConfig` is frozen and validated on construction. A wrong type or a value out of range raises `ConfigError`. `bool` is not accepted where a number is expected, and numbers must be finite.

| Field | Default | Rule |
| --- | --- | --- |
| `budget_tokens` | required | integer ≥ 1 |
| `reserve_tokens` | 2048 | integer ≥ 0, and < `budget_tokens` |
| `pressure_ratio` | 0.70 | 0 < r < 1, and `r × budget ≤ budget − reserve` |
| `initial_new_text_ratio` | 2.5 | > 0 (characters per token) |
| `guard_ratio_cap` | 1.5 | > 0 (characters per token). This is a **cap, not a floor** (§9.4) |
| `guard_margin` | 0.10 | 0 ≤ m < 1 |
| `protect_turns` | true | bool |

`working_limit_tokens` = `budget_tokens − reserve_tokens`. In this version only `protect_turns` affects behaviour; the other fields are validated now and used by §9. The core has no counters, model selection, billing or task scoring.

## 3. Entries, limits and serialisation (implemented)

**Entry fields:**
- **`id`:** fully matches `^[A-Za-z0-9][A-Za-z0-9_.:~-]{0,63}$`. Validation uses `fullmatch`, so a trailing newline is not accepted.
- **`role`:** one of `assistant`, `observation`, `note`, `summary`, `receipt`.
- **`body`:** text that is encodable as UTF-8, so a lone surrogate is rejected.
- **`protected`:** runtime-owned (§5).

Entries are immutable. In the model-visible form, `protected: true` appears only on protected entries.

**Structural limits**, checked on every complete candidate state:

| Limit | Value |
| --- | --- |
| entries | 200 |
| code points per body | 24,000 |
| code points across all bodies | 120,000 |
| UTF-8 bytes of the canonical serialisation | 256,000 |

IDs are unique.

**Canonical serialisation** (`serialize`, `Snapshot.to_json`), deterministic:
- **Function:** `json.dumps({"format": "clm-context/v1", "entries": [...]}, ensure_ascii=False, separators=(",", ":"))`.
- **Key order:** `format`, then `entries`. Within each entry: `id`, `role`, `body`, then `protected` only when true.
- **Layout:** no whitespace between tokens and no trailing newline.
- **Escaping:** the standard JSON escapes of Python's `json` module (`\"`, `\\`, `\n`, `\u00XX` for other control characters). Non-ASCII text is written as UTF-8, not escaped.
- **Byte limit:** applies to the UTF-8 encoding of this string.

**Store and revisions:**
- The store holds ordered entries and a revision number. Revision 0 is the empty state.
- Every accepted change advances the revision by exactly one, whatever its source: a model edit, a host append or a turn materialisation. Rejected and unchanged transactions do not.
- `Snapshot` is an immutable view of one revision. Its `to_dict()` returns a fresh copy.

## 4. Edit transactions (implemented)

A transaction is `apply_edits(operations, expected_revision=N)`. Operations, in their JSON form:

```json
{"op": "delete", "ids": ["a", "b"]}
{"op": "replace", "id": "a", "body": "new text"}
{"op": "add_note", "id": "n1", "body": "text"}
```

Typed equivalents are `Delete`, `Replace` and `AddNote`. The keys must match exactly: no missing keys and no extra keys, such as `role` or `protected`.

**Processing rules:**

1. **Model input:** `operations` and `expected_revision` are treated as model input. Any problem gives a `rejected` result, not an exception.
2. **Stale revision:** `expected_revision` must be an integer equal to the current revision; otherwise the transaction is rejected.
3. **Candidate state:** operations apply in order to a candidate copy of the state. Each operation sees the effects of the earlier ones.
4. **`delete`:** each ID must exist in the candidate and must not be protected. The list must be non-empty and free of duplicates.
5. **`replace`:** the ID must exist and must not be protected. It keeps the entry's role and position and changes only the body.
6. **`add_note`:** creates a `note` at the end. The ID must be valid and outside the reserved namespaces (§5). It must not exist in the candidate, and must not have existed when the transaction started. So an ID deleted earlier in the same transaction cannot be reused until a later transaction. Duplicate IDs are rejected, never renamed.
7. **Atomicity:** any invalid operation, protected target or structural violation of the final candidate rejects the **whole** transaction. Nothing is applied.
8. **No change:** if the final candidate equals the starting state, the outcome is `unchanged`, with no new revision. This covers, for example, replacing a body with the same text, or adding and then deleting a note.
9. **Otherwise:** the outcome is `accepted` and the revision advances once.

**Results** (`EditResult`): the outcome, the base and resulting revision, and ID lists relative to the starting state:
- `removed`: existed before, gone now;
- `added`: did not exist before, so a note added and then replaced is listed only as added;
- `rewritten`: in both states, with a new body.

A rejection carries a reason and, where one applies, a 0-based operation index. `receipt()` gives the compact model-visible form. It never includes removed bodies.

## 5. Protection and reserved namespaces (implemented)

**Protection is runtime-owned metadata.** Only the turn lifecycle (§6) creates protected entries. A model cannot set it, since no operation accepts a `protected` key and `add_note` always creates an unprotected note. A host append cannot set it either, because entries flagged protected are refused. Body text that looks like metadata is ordinary text.

**Reserved namespaces.** A model's `add_note` may not create these IDs:
- **Turn records:** `^t[0-9]+\.(request|answer)(~.*)?$`. The host's runtime-entry append is refused in this namespace too; only the lifecycle uses it.
- **Step records:** `^s[0-9]+\.(act|obs|rcpt)(~.*)?$`. These are host-created runtime entries (§9.2).

Both include collision-suffixed forms (for example `t2.answer~2`) and leading zeros, so no look-alike can be created. Protection never depends on matching an ID pattern; it is stored with the entry.

**No renaming.** IDs are never renamed. A duplicate host or lifecycle ID fails explicitly and changes nothing.

## 6. Turn lifecycle (implemented)

`GuardedContext(config)` owns the store and the turn state. The states are `NOT_STARTED`, `ACTIVE` and `ANSWERED`.

- **`start_turn(request)`:**
  - **Turn 1:** the request becomes the immutable **task anchor**. It is kept outside the working context, is never duplicated as `t1.request`, and is not subject to the body limit.
  - **Later turns:** start only after the previous turn's answer is recorded. The new request is the host-owned **current request**: it is kept outside the working context, so no edit can reach it. It must be a valid body, because it later becomes a record.
  - **On failure:** a failing call changes nothing.
- **`prepare_request()`:** the hook the request builder will call before every request (§9.1). It returns `RequestState(turn, task, current_request, snapshot)`; `current_request` is `None` on turn 1.
  - On its first call in turn N+1, it **materialises** turn N's records in one revision: `tN.request` (for N ≥ 2, role `observation`), then `tN.answer` (role `assistant`). Both are protected when `protect_turns` is true.
  - **It is idempotent:** later calls in the same turn return the same state, with no new records and no new revision.
  - If the records would break a structural limit, it returns a failure, nothing changes, and the records stay pending. Token-budget exhaustion is planned (§9.5).
- **`submit_answer(answer)`:** records the current turn's final answer exactly once. It is allowed only after the turn's first preparation; a second call raises `LifecycleError`. Only this call creates an answer record. Intermediate assistant output, appended as runtime entries, stays ordinary and unprotected.
- **Changes within an active, prepared turn:**
  - `apply_edits(...)`: §4;
  - `append_runtime_entries(entries)`: trusted host entries, unprotected, outside the turn-record namespace, appended in one revision.
- **With `protect_turns = false`:** earlier records are created unprotected and may be edited or deleted. The namespace stays reserved, and the task anchor and current request stay host-owned.
- **Lifecycle misuse** (calls in the wrong state) raises `LifecycleError`. There is no automatic digest, summary or deletion of earlier turns.

## 7. Trust boundary (implemented)

`GuardedContext` and `ContextStore` methods are **trusted host APIs**. They validate everything a model can influence, so a model's edit cannot break the format, the limits, protection or the reserved namespaces. They are **not** a sandbox boundary: code running in the host process can always reach Python internals, so model-generated code must never run in-process.

## 8. Public API (implemented, 0.1)

- `GuardedConfig`, `ConfigError`;
- `GuardedContext`, `TurnState`, `RequestState`, `PrepareResult`, `LifecycleError`, `turn_request_id`, `turn_answer_id`, `REQUEST_ROLE`, `ANSWER_ROLE`;
- `ContextStore`, `Snapshot`, `ChangeResult`;
- `Entry`, `ValidationError`, `serialize`, `is_reserved_id`, `FORMAT`, `ROLES` and the `MAX_*` limits;
- `Delete`, `Replace`, `AddNote`, `Operation`, `parse_operation`, `OperationError`, `EditResult`.

The API is experimental, before 1.0.

## 9. Planned behaviour (not implemented)

### 9.1 Request layout

- **Shape:** a system prompt, plus **one reconstructed user message** containing the task, the working context and the runtime status.
- **First request:** stays in the task.
- **Later requests:** also include the current request.
- **No hidden history replay.**
- **Hook:** the builder calls `prepare_request()` (§6).
- **Open:** the encoding that keeps entry text from forging section markers. The supplied instruction is malformed and needs clarification (decisions.md, U1).

### 9.2 Runtime entries

These are appended after a step is processed:
- `sN.act`: the response text and descriptions of the tool calls;
- `sN.obs`: the delivered results;
- `sN.rcpt`: an edit receipt, when appropriate.

### 9.3 Shared estimator

- **Anchor:** reported input usage.
- **Append-only changes:** use the lowest eligible observed new-text ratio, starting at `initial_new_text_ratio` (2.5). An observation is eligible only when it adds at least 200 tokens.
- **Removals:** use an overall ratio, updated halfway toward the latest observation.

### 9.4 Conservative output guard

- **Estimate:** the base estimate, excluding this step's new entries, plus `ceil(added_characters / r × (1 + guard_margin))`.
- **Ratio:** `r` = the minimum of `guard_ratio_cap` (1.5) and the eligible observed append-only ratios.
- **Holding:** edit calls are never held. Once one non-edit output is held, later non-edit outputs in that response are also held.
- **Coverage:** all framing, calls, receipts and notices are accounted for.
- **Tools:** the initially supported task tools are read-only.

### 9.5 Pressure and recovery

- **Limits:** the working limit is `budget − reserve`. Pressure starts at `pressure_ratio × budget`.
- **Reserve:** one request may use the reserve.
- **Termination:** continued overflow, or exceeding the budget, ends the turn according to the state machine.
- **Reset:** recovery resets on a new user turn.
- **Open:** re-arming after a return below the limit must be resolved against existing clm-lib behaviour before implementation (U3).

### 9.6 Counters and the free-step rule

- **Task steps:** 60 per turn.
- **Call cap:** a 144 main-model/provider-call cap per turn. Its exact accounting meaning is undecided (U2).
- **Free-step rule:** under the supplied design, **a response whose first tool call is `edit_context` is a free task step, even if later calls in the same response do task work.** It is not limited to pure-edit responses. Every call still incurs its real cost and the relevant call counters.

### 9.7 Multiple tool calls

Several calls in one response are processed **in order**, not concurrently.

### 9.8 Adapters

Planned, and kept out of the core import path:
- an Anthropic adapter, with scripted end-to-end tests (plan chunk 3);
- a Pydantic AI capability (plan chunk 4).

Support for tools with side effects is future work.
