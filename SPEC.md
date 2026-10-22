# guarded-clm specification: `ClmContext`

**Status:** 0.1.0.dev0 (15 October 2026). It describes the implemented behaviour of `ClmContext` and `ClmStepLimit`. The trace records name the design `clm_lite_v3_3`.

**This document is normative.** The library must behave exactly as described here. `tests/test_golden.py` checks that against stored transcripts (section 15). Where this text and the transcripts disagree, the transcripts win, and this text is the bug.

**Version history:**
- **0.1.0.dev0 (2026-10-15):** the first specification of `ClmContext`. It replaces an earlier, unreleased core in this repository, which had a different design (docs/decisions.md, P1).
- **0.2.0.dev2 (2026-10-22, extended 2026-10-23):** two opt-in segmented request layouts with an optional context cache point. `"segmented"` (section 3.1, decisions P8) keeps the task first, so its cached prefix lasts within a turn. `"segmented_task_last"` (section 3.2, decisions P9) moves the task section after the working context, so the prefix also lasts across user turns; it tells rebuilt requests, runtime notes and user turns apart by runtime-set metadata, never by their text (section 4.2, decisions P10 and P12). `add_context_note()` adds a marked note (section 2). The default layout is unchanged; it is intended to become `"segmented_task_last"` in a later release, after live validation (decisions P11).

## Contents

1. [What it is](#1-what-it-is)
2. [Public API](#2-public-api)
3. [The request](#3-the-request)
4. [The working context](#4-the-working-context)
5. [Turns and protected entries](#5-turns-and-protected-entries)
6. [Edits](#6-edits)
7. [The output guard](#7-the-output-guard)
8. [Size estimates](#8-size-estimates)
9. [Notices and endings](#9-notices-and-endings)
10. [Steps and calls](#10-steps-and-calls)
11. [Trace records](#11-trace-records)
12. [State](#12-state)
13. [Known properties and limits](#13-known-properties-and-limits)
14. [The default settings and their assumptions](#14-the-default-settings-and-their-assumptions)
15. [Transcript tests](#15-transcript-tests)
16. [Lineage](#16-lineage)

## 1. What it is

ClmContext is a Pydantic AI capability. With it, the model manages its own working context inside a fixed token budget.

The model doesn't receive the usual growing message history. Every request is rebuilt from a list of **entries**: the agent's actions, the tools' observations, receipts and the model's own notes. The model can delete, shorten or replace entries and add notes with one tool, `edit_context`, and it decides what to keep.

The runtime keeps this safe:

- **It validates every edit.** An edit is all or nothing.
- **It protects earlier turns.** Each earlier request and the answer given to it stay in the context, and the model can't edit them.
- **It holds back tool outputs that would overflow.** A guard checks each output before it is added, using a conservative size estimate.
- **It reports the size.** The request carries its size, and a PRESSURE notice from 70% of the budget.
- **It enforces the limit.** One request may use a 2,048-token reserve; a second one over the limit ends the run.

**This is working-context management, not a security guardrail.** It doesn't filter content, detect prompt injection or sandbox anything; "safe" above means only that the runtime enforces these rules on the working context. Section 16 has the lineage.

## 2. Public API

```python
from guarded_clm import CALL_CAP, clm_context
agent = Agent(model, capabilities=[clm_context()])
await agent.run(prompt, conversation_id=cid, message_history=history, usage_limits=UsageLimits(request_limit=CALL_CAP))
```

- **`clm_context(*, budget_tokens=32_000, max_steps=60, call_cap=None, end_over_budget=False, max_output_chars=8_000, limits=None, on_record=None, id="clm_context", request_layout="single", cache_point=None)`** returns a `CombinedCapability` of `ClmStepLimit(max_steps, call_cap=cap)`, then `ClmContext(...)`, in that order. `cap` is `call_cap`, or `2 × max_steps + 24` (144 by default). Pydantic AI flattens a combined capability into its leaves, so this is the same as listing the two.
- **`ClmContext(*, budget_tokens=32_000, end_over_budget=False, max_output_chars=8_000, max_steps=60, call_cap=144, limits=None, on_record=None, id="clm_context", request_layout="single", cache_point=None)`:**
  - `request_layout` is `"single"` (the default, as tested), `"segmented"` (section 3.1) or `"segmented_task_last"` (section 3.2). Any other value raises `ValueError`.
  - `cache_point` is `"5m"`, `"1h"` or None (the default). It needs one of the two segmented layouts, or `ValueError` is raised.
  - `max_steps` and `call_cap` are only shown in the runtime status. `ClmStepLimit` enforces them.
  - `max_output_chars` caps the `edit_context` tool's result (section 6.4).
  - `limits` is a `ContextLimits(max_file_bytes=256_000, max_entries=200, max_body_chars=24_000, max_total_chars=120_000)`.
  - `id` is the capability id (L9). Pydantic AI requires ids to be unique within an agent's capabilities, so pass another one when a different capability already uses `clm_context`. `for_run` gives each run's copy the same id, and the edit tool finds that copy by it.
- **`ClmStepLimit(max_steps=60, *, call_cap=None, finalize=True)`:** section 10.
- **`add_context_note(request, text) -> UserPromptPart`** (decisions P12): appends `UserPromptPart(text)` to a `ModelRequest`, with the text unchanged, and adds the part's index to `request.metadata["guarded_clm_notes"]`, keeping other metadata. ClmContext shows such a part as a note (section 4.2). `ClmStepLimit` adds its finalize notice with it. A host adds its own notes with it from a capability whose `before_model_request` runs before ClmContext's.
- **Errors**, all `pydantic_ai.exceptions.AgentRunError`:
  - `ContextLimitError`;
  - `BudgetExceededError`, a subclass of `ContextLimitError`;
  - `StepLimitError`.

  The call cap raises Pydantic AI's `UsageLimitExceeded`.
- **Constants:**
  - `BUDGET_TOKENS` (32,000);
  - `MAX_STEPS` (60);
  - `CALL_CAP` (144);
  - `RESERVE_TOKENS` (2,048).

**The host must:**

- **Pass both `conversation_id` and `message_history`** on every turn of a conversation. Pydantic AI can take the id from `message_history`, and the working context continues with the id alone, but the previous turn's answer is only taken from the history (section 12).
- **Pass `usage_limits=UsageLimits(request_limit=CALL_CAP)`** (or its own cap). Pydantic AI's default `request_limit` of 50 would otherwise end runs before the tested cap of 144.
- **Keep `ClmStepLimit` in front of `ClmContext`.** Its finalize notice must already be in the request when `ClmContext` rebuilds it, and `clm_context()` does this.

**Internal identifiers** (not sent to the model):

- the capability id, `clm_context` by default (the `id` argument);
- the step limit's id `clm_step_limit`;
- the toolset id `clm_context_tools`.

The transcript comparison ignores them. The step limit's and the toolset's ids are fixed.

## 3. The request

**`before_model_request`** folds everything since the last rebuilt request into the context (section 4.2). It then replaces the request with exactly one `ModelRequest`, whose parts are:

1. the first `SystemPromptPart` found in the history, if there is one;
2. one `UserPromptPart`:

```
<task>
{task}
</task>

<working_context>
{entries}
</working_context>

<runtime_status>
{status}
</runtime_status>
```

**`{task}`** is the conversation's first request. In a later turn whose request differs from the first, it is `{first}\n\nCurrent request:\n{current}`. The task text is `.strip()`ped.

**`{entries}`** is one JSON object per line, `{"id": ..., "role": ..., "body": ...}` (`ensure_ascii=False`):

- a protected entry (section 5) gets `"protected": true` after its body;
- every `<` is written as the six characters `\u003c` (backslash, u, 0, 0, 3, c: the JSON escape, applied after `json.dumps`), so entry text can't forge section tags.

**`{status}`** is these lines, then any notices, one per line:

```
step {step + 1}; task steps used this turn {task_steps} of {max_steps} (edit_context steps are free); model calls this turn {calls_this_run} of {call_cap}
request size: about {tokens:,} of {limit:,} tokens ({round(100 * tokens / limit)}% of the limit); budget {budget:,}
context revision {revision}, {n} entries
```

- `task_steps` is the steps taken this run minus the edit steps taken this run.
- The notices are, in this order:
  - the notes found in the request (section 4.2), such as the finalize notice;
  - PRESSURE (section 9);
  - OVER LIMIT (section 9).

**How the request is built:**

1. A draft is built with `tokens = 0` and measured.
2. The request is built with the draft's estimate and measured. That measurement decides PRESSURE and the limit checks.
3. If notices were added, both steps run again with them.

**The instructions** are added to the agent's instructions on every request (`get_instructions`). They are `INSTRUCTIONS` in `instructions.py`, word for word as tested. That includes wording that no longer applies literally, such as "(apart from files you write)". Only `{max_entries}` and `{max_body}` vary, from `limits`.

**The edit tool:**

- **Name:** `edit_context`, with one parameter, `ops: list[dict[str, Any]]`.
- **Docstring:** exactly as in `capability.py`. It is the tool's description and parameter text.

### 3.1 The segmented layout (opt-in)

With `request_layout="segmented"` (decisions P8), the request is built exactly as above, except that the `UserPromptPart`'s content is a list of items instead of one string:

1. **the task head:** `<task>\n{task}\n</task>\n\n<working_context>\n`;
2. **one item per entry,** in order: its JSON line, as above, followed by `\n`;
3. **with `cache_point` set,** a Pydantic AI `CachePoint(ttl=cache_point)`;
4. **the tail:** `</working_context>\n\n<runtime_status>\n{status}\n</runtime_status>\n`. With no entries it starts with an extra `\n`, as above.

**The joined text items are exactly the single layout's text,** so the sections, entries, markings, escaping and status lines are the same. A provider adapter sends each item as its own content block. The `CachePoint` marks the block before it: the last entry, or the task head when there are no entries. Models without prompt caching drop the marker (Pydantic AI).

**What a provider with prompt caching can reuse** (its documented rules: breakpoints at block boundaries, an exact prefix, a 20-block lookback):
- **Ordinary steps:** a step appends its entries after the existing ones, so a request's prefix through the previous request's last entry is unchanged, and its cache point is a few blocks further on.
- **Status changes:** the status comes after the marker, so a status-only change invalidates nothing before it.
- **Edits:** an accepted edit changes the prefix from the first entry it changes or removes.
- **New turns:** a new user turn changes the task head (section 5), so it invalidates every block after it.

**What differs from the single layout:**
- **The request** is not byte-identical: the same characters arrive as several blocks, which a model may treat differently.
- **The size estimates** (section 8) measure the serialized request, so the list form counts its framing: about three characters per item, plus the marker.
- **Decisions near a threshold** can therefore differ. The budget, the limit and the rules don't.

### 3.2 The task-last segmented layout (opt-in)

With `request_layout="segmented_task_last"` (decisions P9), the items are those of section 3.1 with the `<task>` section moved, unchanged, after the working context:

1. **the head:** `<working_context>\n`;
2. **one item per entry,** in order, as in section 3.1;
3. **with `cache_point` set,** a Pydantic AI `CachePoint(ttl=cache_point)`;
4. **the tail:** `</working_context>\n` (with no entries, `\n</working_context>\n`), then `\n<task>\n{task}\n</task>\n\n<runtime_status>\n{status}\n</runtime_status>\n`.

**The text is the section 3.1 text with the `<task>` section moved:** the same sections, entries, markings, escaping and status lines, and the same characters and items. Only their order differs.

**Why the order matters:** everything that changes between requests without an edit (the task text at a new turn, and the status) is now in the tail, after the cache point. Before it, there are only the fixed `<working_context>\n` and the entries, which only grow at the end until an edit changes them.

**What a provider with prompt caching can reuse** (the rules in section 3.1):
- **Ordinary steps and status changes:** as in section 3.1.
- **New turns:** a new turn appends `t{N}.answer` and, from the second turn, `t{N}.request` after the existing entries (section 5), and changes only the tail's task text. So the previous request's cache point, at its last entry, is still an unchanged prefix, one or two blocks back.
- **Edits:** as in section 3.1, an accepted edit changes the prefix from the first entry it changes or removes. The prefix up to a later entry can be reused only where an earlier request ended its entries there, within the lookback, and that entry is still alive. After an early edit, often only the first request's `<working_context>\n` head qualifies.
- **Rejected edits** change nothing, so the next request is append-only.

**What differs from section 3.1:**
- **The model sees the task after its working context,** not before it. The instructions name the three sections and don't depend on their order, but a model may weigh a request differently by position, so behaviour is not claimed to be the same.
- **Sizes and decisions are the same:** the same characters and the same number of items serialize to the same length, so every estimate, notice and ending matches section 3.1 (section 13.17).
- **Turn detection** uses provenance, not text (section 4.2): the rebuilt request carries `ModelRequest.metadata == {"guarded_clm_rebuilt": True}`, set after it is measured, and a runtime note is listed under `metadata["guarded_clm_notes"]` by `add_context_note()` (decisions P10, P12). Pydantic AI keeps that field in the history and doesn't send it to the model.
- **Host cache settings:** the library adds only the context's `CachePoint` and never changes the model settings. The recommended Anthropic settings are in the README and decisions P11.

## 4. The working context

### 4.1 Entries

**What an entry is:**

- **Fields:** `Entry(id, role, body)`.
- **Ids:** `^[A-Za-z0-9][A-Za-z0-9_.:~-]{0,63}$`, unique.
- **Roles:** `assistant`, `observation`, `note`, `summary` or `receipt`.

**How revisions are stored:** `ContextStore` keeps every revision: its number, entries, hash, parent, source and step. Revision 0 is empty. Every append or accepted edit adds a revision.

**Runtime entries**, added by the runtime and never by the model:

| Id | Role | When |
|---|---|---|
| `sN.act` | assistant | A step's action: `thought: {text}` (when the response had text) and one `{tool}({args JSON})` line per call, in order |
| `sN.obs` | observation | The step's results, as `[tool]\n{text}` blocks joined by blank lines, after the guard (section 7). `(no output)` if none |
| `sN.rcpt` | receipt | The step's edit receipt, when an edit ran in it (the last one, if more than one ran) |
| `tN.answer` | assistant | Turn N's final reply (a response with no tool calls, non-empty after `strip()`), added when the next request is built |
| `tN.request` | observation | Turn N's request, added when turn N+1 starts, unless it equals the first request |

**Id collisions:** when a runtime id already exists, for example because the model added a note with that id, the new entry gets `~2`, `~3` and so on. Section 13 covers what that means for protection.

### 4.2 Folding a step in

The new messages are those after the last rebuilt request: in the single and segmented layouts, the last request with a user prompt that starts with `<task>\n`; in the task-last layout, the last request whose metadata holds `guarded_clm_rebuilt` (section 3.2, decisions P10). They are taken in order:

- **A response with tool calls:**
  1. `step += 1`.
  2. The action entry is built.
  3. The tool results are taken from the next request (`ToolReturnPart` and `RetryPromptPart`): by call id, in the order the calls were listed, then any unmatched results.
  4. The results pass through the guard.
  5. `sN.act`, `sN.obs` and `sN.rcpt` (if a receipt is pending) are appended as one revision.
- **A response without tool calls:** its text becomes `t{turn}.answer`, if non-empty.
- **A request's user prompt parts:**
  - **in the task-last layout** (decisions P12), by provenance only, never by the text:
    - a part whose index is listed in its request's `metadata["guarded_clm_notes"]` (`add_context_note()`) is a note, shown in the status;
    - a part of a request with `metadata["guarded_clm_rebuilt"]` is the rebuilt request (it is never among the new messages);
    - any other part is a new turn (section 5), whatever its text, including openings like `<task>`, `<working_context>` or `[context manager]`;
  - **in the single and segmented layouts,** by the tested text rules (section 13.19):
    - a part starting with `[context manager]` is a note, shown in the status;
    - a part starting with `<task>\n` is skipped;
    - any other part is a new turn (section 5).

**How results are rendered:**

- a tool return as `model_response_str()`;
- a retry as `model_response()`, under its tool name, or `retry` when it has none.

That includes validation errors and unknown-tool messages.

**The ordering rule:** a step's entries are appended after its tools ran. So an edit can never remove the current step's own output; it can be removed in a later step.

## 5. Turns and protected entries

**When a turn starts** (a user prompt that isn't a note and isn't a rebuilt task):

- the first ever becomes `first_task`;
- otherwise, the previous request becomes `t{turn}.request`, unless it was the first request;
- then `turn += 1`, the current request is set, and the over-limit allowance resets (section 9).

**What is protected:** an id matching `^t\d+\.(request|answer)$`. The match is on the id alone, whatever the role or origin of the entry.

**What protection does:**

- **Marking:** protected entries are marked in the request.
- **Refusal:** an `edit_context` call is refused as a whole when one of its operations deletes, or replaces the body of, a protected id that is present when the call starts. In that case:
  - **Nothing changes**, and the edit still counts as an edit step.
  - **The receipt** is `context.json rejected: protected ids [...]; the previous context is kept.`, with the ids sorted and de-duplicated.
  - **The result** is `rejected: {n} operation(s); protected ids [...]`.
  - **The trace** gets a `lite_edit` record (status `rejected`), then `lite_protected_refused` with the ids.
- **What isn't refused:**
  - an operation naming a protected-looking id that isn't present goes through the normal checks (so it is rejected as unknown);
  - `add_note` may use a protected-looking id.

## 6. Edits

### 6.1 Operations

`ops` is applied in order to a copy of the entries:

- **`{"op": "delete", "ids": [...]}`:** every id must exist, or the edit is rejected with `unknown ids [up to 5, sorted]`.
- **`{"op": "replace", "id": ..., "body": ...}`:**
  - the id must exist, or the edit is rejected with `unknown id '...'`;
  - the body becomes `str(body)` (`""` if missing);
  - the role is kept.
- **`{"op": "add_note", "id": ..., "body": ...}`:** appends `Entry(str(id), "note", str(body))`.
- **Anything else:** `unknown op '...'`.

Arguments that aren't a list of objects fail Pydantic AI's validation. They come back as a retry, not an edit, and don't count as an edit step.

### 6.2 Validation

The edited entries are serialized as clm-lib's file format, `{"format": "clm-context/v1", "entries": [...]}` (`json.dumps(..., ensure_ascii=False, indent=1) + "\n"`), and parsed back with its checks, in this order:

1. the size in UTF-8 bytes (`max_file_bytes`);
2. the entry count (`max_entries`);
3. per entry: the id pattern (`entry {i}: invalid id '...'`), duplicates (`duplicate id '...'`), the role, and the body length (`entry '...': body {n} chars; limit {max}`);
4. the total body characters (`max_total_chars`).

**Validation covers every entry, runtime entries included.** So once a runtime entry breaks a limit, every edit is rejected until one deletes that entry. For example, a tool output over 24,000 characters that fit the budget does this (section 13).

### 6.3 Outcomes

| Outcome | When | Receipt |
|---|---|---|
| `rejected` | Any check fails (first failure wins) | `context.json rejected: {reason}; the previous context is kept.` |
| `unchanged_write` | Valid, and identical to the current entries | `context.json unchanged write; the previous context is kept.` |
| `accepted` | Valid and different: committed as a new revision (source `model_edit`) | `context.json accepted as revision {n}: {k} removed [...], {k} added [...], {k} rewritten [...].` (up to 20 ids each) |

**Every edit:**

- counts one edit step;
- sets the step's pending receipt (a later edit in the same step replaces it);
- records `lite_edit`.

**Snapshots for the guard:** after each edit, the entries are snapshotted for the guard under the call id.

### 6.4 The tool result

The result is `{status}: {n} operation(s)`, plus `; {reason}` if there is a reason. It is capped at `max_output_chars`:

- when it is longer, the head (3/5) and tail are kept;
- `\n[... {k} characters omitted ...]\n` goes between them.

## 7. The output guard

The guard runs while a step is folded in (section 4.2), for each result in listed order.

**Which outputs it covers:**

- **`edit_context` results are never held back.** After one, the guard's context becomes the snapshot taken after that edit.
- **Every other output is measured before it is added.** The guard's context starts as the entries before the response's tools ran (snapshotted after each response).

**The test for each output:** it fits if the projected next request, with the step's action, the observation so far plus this block, and the pending receipt, is at most the **limit** (`budget − 2,048`).

- If it fits, it is added.
- If not, it and **every later non-edit output in that response** are replaced with:
  ```
  [{tool}]
  Held back: {tool}({args}), about {N:,} tokens; only {M:,} tokens free under the limit. Tidy your context, then call it again.
  ```
  - `args` is the call's JSON arguments, cut to 79 characters plus `…` when longer than 80.
  - `N` is the projection with the block minus the projection without it (at least 0).
  - `M` is the limit minus the projection without it (at least 0).

**Projection (the conservative estimate):**

- **The base:** the shared estimate (section 8) of the request without this step's new entries (`sN.*`). That is measured as the request would be built: with the status block, and with a PRESSURE line if that estimate reaches 70% of the budget.
- **The added part:**
  - `added` = the characters of the request with the new entries, minus those without them, both with the base's status block;
  - the projection = `base + ceil(added / r × 1.10)`.
- **The PRESSURE correction:** if the projection reaches 70% of the budget and the base doesn't, a PRESSURE line would appear too. So `added` is measured again with that line in the status, and the projection recomputed.

**r, the guard's new-text ratio:**

- `r = min(1.5, lowest observed)` characters per token, and 1.5 before any observation.
- **How it is observed:** after each call, the request just answered is compared with the previous answered request. If it extended that request, the ratio is `(characters added) / (reported tokens added)`, and the lowest is kept. A request extends the previous one when:
  - the task is the same;
  - its entry lines (unmarked rendering) start with the previous request's lines;
  - both its characters and its reported tokens are higher.
- Scripted models (`FunctionModel`, `TestModel`) report made-up usage. The shared estimator ignores it, but the r observation uses it as reported.

**Refetches:** when a later call has the same tool and argument JSON as a held-back one, a `lite_refetch` record is written once, and the pair is forgotten.

**What the guard doesn't cover:** growth from the model's own text (thoughts, long tool arguments), and new turns. The over-limit rule (section 9) is the backstop for those.

## 8. Size estimates

Request size is estimated per conversation (`SizeEstimator` with a `Calibration`), with characters measured by `request_chars`:

- the messages as JSON, without their `instructions`;
- plus the instructions actually sent;
- plus the tool definitions as JSON.

| Method | When | Tokens |
|---|---|---|
| `fixed_ratio` | Scripted models (`model.system` is `function` or `test`) | `round(chars / 4)` |
| `anchored` | The request is no smaller than the last one (counted as "appended"), and calibrated | last reported input + `round(added chars / new-text ratio)` |
| `calibrated` | Otherwise | `round(chars / ratio)`; before any usage, `round(chars / new-text ratio)` |

**How the ratios update**, after each call with real usage:

- **The overall ratio** moves halfway to `chars / reported`.
- **The new-text ratio** (from 2.5) keeps the lowest `added chars / added tokens`, for additions of at least 200 tokens, from a request counted as appended (no smaller than the last one). An observation below 0.5 characters per token (`MIN_PLAUSIBLE_NEW_TEXT_CHARS_PER_TOKEN`) is ignored as implausible, so it never lowers the ratio (section 13.16, decisions P7).

**The segmented layouts** (sections 3.1 and 3.2) are measured as sent. Their list content serializes with a few more characters than one string, about three per item plus the marker, so their estimates are slightly higher. The two segmented layouts serialize to the same length.

**This is the shared estimator.** The status line, PRESSURE and the limit checks use it. The guard uses it for its base, and its own r for the added text.

## 9. Notices and endings

**PRESSURE:** `PRESSURE: the request is about {tokens:,} tokens, {pct}% of the {budget:,}-token budget. Manage the working context before it runs out.` It is shown when the estimate is at least 70% of the budget.

- Its number is the second measurement (section 3).
- The size line shows the final one, so the two can differ slightly.

**When a request is over the limit** (`budget − 2,048`):

- **The run ends with `ContextLimitError`** (after a `context_limit` record) when either:
  - the previous request already used the reserve (reason "still over the limit after one over-limit request");
  - or the estimate is over the budget itself (reason "over the budget itself").
- **Otherwise, the request uses the reserve.** The request carries `OVER LIMIT: this request uses the 2,048-token reserve. Reduce the working context in this step; if the next request is still over the limit, the run ends.`, and `lite_over_limit` is recorded.

**A request within the limit** clears the over-limit flag. A new turn also clears it.

**Reported input over the budget:**

- **With `end_over_budget=True`:** a call whose reported input tokens are over the budget records `context_limit` and raises `BudgetExceededError`.
- **Without it:** the call is accepted. The next estimate is anchored on that reported input, so it is usually over the budget, and the run then ends as above.

## 10. Steps and calls

`ClmStepLimit` counts per run (one `agent.run`, a user turn), and `for_run` gives fresh counts.

**Counting:**

- **Model calls:** every response, edits included.
- **Task steps:** every response, except one whose first tool call is `edit_context`.

**Before each request**, in this order:

1. If task steps ≥ `max_steps`, it raises `StepLimitError("the task-step limit of {n} was reached")`.
2. If calls ≥ `call_cap`, it raises `UsageLimitExceeded("the LM-call cap of {n} was reached")`.
3. If `left = min(max_steps − steps, call_cap − calls)` is at most 3, and the last message is a request, it appends the user prompt part `[context manager] {left} step(s) left: finish now and give your final answer.` to that request with `add_context_note()`, which also records it in the request's metadata (section 2). ClmContext then shows it as a notice. The request is the next request's tool results, or the user's own prompt when a run starts with at most 3 steps left.

ClmContext keeps its own step count (the `sN` numbering, over the whole conversation) and its own edit-step count. It shows the per-run difference in the status.

## 11. Trace records

`on_record` receives dicts, each with `"variant": "clm_lite_v3_3"` first. That is the tested design's name, kept as a compatibility contract so tools that read the records keep working. The record types are below.

**About `step`:** a record written while a tool runs carries the previous step's number, because the step is folded in afterwards.

| `type` | Fields | When |
|---|---|---|
| `lite_guard_check` | `step, r, shared_base, added_chars, conservative_added, projected` | Each guard projection |
| `lite_held_back` | `step, tool, args, estimated_tokens, free_tokens, r, shared_estimate, conservative_estimate` | An output held back |
| `lite_refetch` | `step, tool, args` | A held-back call made again |
| `lite_parallel_calls` | `calls` (names after the first) | A response with more than one tool call |
| `lite_edit` | `step, status, reason, revision, exit_code (0), timed_out (false), code (the ops as JSON), entries` | Each edit |
| `lite_protected_refused` | `step, ids` | An edit refused for protected ids |
| `lite_over_limit` | `estimated_tokens, limit_tokens` | A request using the reserve |
| `context_limit` | `source ("agent"), estimated_tokens, limit_tokens, reason` or `source, reported_input_tokens, budget_tokens` | The run ending on the limit or a reported overshoot |

## 12. State

**Where state lives:** in memory, in the `ClmContext` instance given to the agent, shared by its per-run copies.

**Per conversation** (the key is `ctx.conversation_id`, or `ctx.run_id`, or `"session"`; at most 512 conversations, least recently used evicted), it keeps:

- the session: the context store, step, turn, first and current request, over-limit flag, pending receipt and counters;
- the size estimator.

**Per session object** (by `id(session)`, never evicted), it keeps the guard state and the density.

**Per run:** the calls this run, and the step counters at the run's start.

**The message history is replaced too.** Pydantic AI keeps the rebuilt request, so `result.all_messages()` after a turn holds only the last rebuilt request (system prompt plus the `<task>`, `<working_context>` and `<runtime_status>` message) and the responses after it. It isn't a readable transcript of the conversation. A service that shows or stores chat history needs its own record of the turns.

**Consequences:**

- **One process, one context.** A conversation's turns must reach the same process and the same agent object. A new process starts the conversation fresh, and the message history alone doesn't restore its working context. What it does recover: everything after the last rebuilt request is folded in as new, so only the last answer survives, as `t0.answer`.
- **Without a `conversation_id`,** Pydantic AI takes the id from `message_history`, or makes a new one.
- **The context lives in the session.** A turn run with the same `conversation_id` but no `message_history` still continues the conversation's working context (the `conversations` scenario).
- **But the previous answer is lost without the history.** A turn's final answer becomes `tN.answer` only when it is folded in from the messages of the next run (section 4.2), so it is only taken from `message_history`. Without the history, it is never added, and nothing protects it.
  - **The transcript evidence:** in the `conversations` scenario, conv-1's third turn runs without history. Its request has `t2.request` but no `t2.answer`.
  - **What to do:** pass both `conversation_id` and `message_history` on every turn (section 2).

## 13. Known properties and limits

Each of these is the implemented behaviour, kept exactly. Changing any of them is a design change (AGENTS.md, "Changing behaviour").

1. **Tools with side effects.** The guard holds back an output after the tool has run. With read-only tools, the model fetches it again later. A tool that sends, writes or pays would run again when called again.
2. **Parallel edits race.** `edit_context` is a sync tool, so Pydantic AI runs it in a worker thread. Two edits in one response run concurrently, their order isn't fixed, and only the last receipt is kept.
3. **Protected-looking ids:** the model can `add_note` with an id such as `t3.answer`. That note is then protected, and the real answer, added later, becomes `t3.answer~2`, which isn't protected.
4. **Oversize runtime entries block edits.** A tool output longer than `max_body_chars` (24,000), or a context over `max_total_chars`, makes every edit fail validation until one deletes the offending entry.
5. **Protected text grows with the conversation:** every earlier request and answer stays in the context, protected, so a long conversation's protected history takes a growing share of the budget. There is no policy for old turns (no digest or eviction).
6. **State is per process, in memory** (section 12). Guard and density state is keyed by `id(session)` and never evicted. A session created after an eviction could, in principle, reuse a freed id's stale state.
7. **Wording:**
   - receipts say `context.json`, though there is no file;
   - the instructions mention files;
   - both are as tested.
8. **The call cap** raises `UsageLimitExceeded` from `ClmStepLimit`, and the host's `UsageLimits` may also stop the run.
9. **The status's task-step count can drift from the step limit.**
   - **The two rules:**
     - the status subtracts one for every `edit_context` call (section 3), including an edit that isn't the response's first call, two edits in one response, and a refused edit;
     - `ClmStepLimit` frees only a response whose first tool call is `edit_context` (section 10).
   - **What it means:** the status can show fewer task steps than the limit has counted, and even a negative number. The limit and the finalize notice use `ClmStepLimit`'s own count, so only the number shown to the model drifts.
   - **An example,** with `max_steps` 3:
     - response 1 calls a task tool, then `edit_context`;
     - response 2 makes two edits;
     - response 3 calls `edit_context`, then a task tool;
     - response 4 answers.

     `ClmStepLimit` counts one task step (response 1). The four requests' status lines show 0, 0, −1 and −1 task steps used.
10. **Images aren't shown to the model.**
    - **From a tool:** an image return, binary or URL, renders as an empty observation (`[tool]` and nothing after it). A mixed list keeps only its text.
    - **In a prompt:** the image is dropped from `<task>`, and only the prompt's text is kept.
11. **Some malformed edits crash the run.** `ops` is `list[dict[str, Any]]`, so an edit whose `ids` holds objects (for example `{"op": "delete", "ids": [{"id": "s1.obs"}]}`) passes the schema. It then raises `TypeError` (unhashable type), which ends `agent.run`, instead of coming back as a rejected edit.
12. **A tool that raises ends the run.** Any exception other than `ModelRetry` propagates out of `agent.run`.
13. **Pydantic AI's `TestModel` loops until a limit ends the run.**
    - **Why:** `TestModel` calls every tool while the history holds no `ModelResponse`, and a rebuilt request never holds one.
    - **With only `edit_context`:** every response is a free edit step, so the run ends at the call cap (`UsageLimitExceeded`), after 144 `lite_edit` records.
    - **For tests:** use `FunctionModel`, as the transcript tests do.
14. **Request-size estimates depend on how a timestamp serializes.**
    - **Why:** `request_chars` (section 8) measures the messages as JSON, timestamps included, and Pydantic AI writes a timestamp at microsecond 0 without its fraction, 7 characters shorter.
    - **What it means:** about one timestamp in a million can move an estimate by a few tokens, and so the status, PRESSURE, the guard and the limit checks.
    - **The transcript tests** freeze the clock for this reason (section 15).
15. **An output that can never fit is held back every time.**
    - **Why:**
      - an output is added only if the projected request fits the limit (section 7), and its new text is priced at `ceil(characters / r × 1.10)` tokens;
      - r is at most 1.5 and never rises (it keeps the lowest ratio observed);
      - so an output longer than about `(limit − base) × r / 1.10` characters is held back however much the model tidies, where `base` is the request without it.
    - **What it means:** the model is asked to tidy and call again, and it may tidy and refetch until the step limit or the call cap ends the run.
    - **An example:** the `never_fits` scenario uses a 6,000-token budget (limit 3,952) and a 7,000-character output: it is held back before and after the model tidies.
    - **At the defaults it doesn't arise for capped outputs:** with a 32,000 budget (limit 29,952) and tool outputs capped at 8,000 characters, an output costs at most about 5,900 tokens at r = 1.5, far below the room left once the context is tidied.

16. **Implausible new-text observations are ignored.**
    - **Why:** a request counts as appended whenever it is no smaller than the last one (section 8), and the new-text ratio only ever moves down. When an edit removes about as much text as the step adds, the request can grow by a few characters while the reported input rises by hundreds of tokens. Without a floor, that observation would drop the ratio far below any real text density, and it would stay there.
    - **The rule:** an observation below 0.5 characters per token is ignored, so it never lowers the ratio (decisions P7). Every other observation is learnt from as before, including those from steps that grew after an edit, at plausible ratios.
    - **What it prevents:** later anchored estimates many times too large, and an over-limit ending ("over the budget itself") although no reported input came near the budget. The guard was never affected: its own r learns only from extending requests (section 7).
    - **An example, with synthetic numbers:** a request that grows by 40 characters while the reported input rises by 250 tokens observes 0.16, which is ignored. With a ratio of 2.0 learnt earlier, a later request adding 30,000 characters is estimated at 15,000 tokens more than the last reported input. At 0.16 it would have been 187,500 more.
    - **Not covered:** a step that grew after an edit can still teach a plausible but lower ratio than the new text's true density, because the appended test compares sizes only. Such estimates err high, which is the conservative side.

17. **The segmented layout's figures are slightly higher** (section 3.1, decisions P8).
    - **The size:** its estimates count the list content's framing. Over the 32 transcript scenarios run in both layouts, each request's estimate was 9 to 69 tokens higher (median 22; at most 1.4%).
    - **What stayed the same:** in every scenario the trace record types and statuses, the calls and the endings were the same, and every request's text was identical apart from the estimate figures. Those are the status line, PRESSURE lines, held-back notes and ending messages.
    - **The provider side:** how a provider tokenizes separate blocks is not known offline. Its reported usage calibrates the shared estimator as usual (section 8).
    - **The task-last layout** (section 3.2, decisions P9) has exactly the same figures and decisions as the segmented layout: in all 32 scenarios its records and runs equal the segmented layout's, and its texts are theirs with the `<task>` section moved.

18. **In the task-last layout, user turns are recognised whatever their text** (decisions P10).
    - **How:** only a request carrying the runtime's metadata key is a rebuilt request, and only a part listed by `add_context_note()` is a note (decisions P10, P12). A turn that opens with `<working_context>\n`, `<task>\n` or `[context manager]`, such as a pasted example and a question, reaches the next request verbatim as the current request and becomes the protected `t{N}.request` at the next turn. The text is not escaped, stripped or rejected.
    - **Notes:** the finalize notice stays a note, on the user's own request and on tool-result requests. A note doesn't persist in the history, because the rebuild replaces the request that carried it; where such a request is kept, its metadata survives the JSON round trip.
    - **Migration:** a host that added notes by starting a user prompt part with `[context manager]` must use `add_context_note()` in this layout. An unmarked part is user text there, so it starts a turn.
    - **What it needs:** the history passed back must keep `ModelRequest.metadata`, as `result.all_messages()` and its JSON form do. A host that keeps only text, or a conversation that changes layout, isn't supported. A capability between `ClmStepLimit` and ClmContext that reorders a request's parts would invalidate the note indices.

19. **In the single and segmented layouts, text decides a user prompt's origin,** as tested (section 4.2):
    - **a genuine turn that opens with `<task>\n` is lost:** it is taken for the rebuilt request, so the turn is skipped, the previous answer isn't recorded and the question never reaches the model (open question O17);
    - **a genuine turn that opens with `[context manager]` is taken for a note:** it is shown once in the status and never becomes a turn (O18);
    - **unchanged there:** both are kept so that their transcripts stay reproducible. A note added with `add_context_note()` is a note in these layouts too. The task-last layout has neither issue (13.18).

## 14. The default settings and their assumptions

The defaults are the settings the design was built and tested with:

- **Budget:** 32,000; limit 29,952.
- **Steps and calls:** `max_steps` 60, `call_cap` 144.
- **`end_over_budget`:** false.
- **Context limits:** the defaults (section 4).
- **Tools:** read-only, with **every output capped (8,000 characters) by the tools themselves.** The library doesn't cap tool outputs (docs/decisions.md, L8).

This repository's own verification is offline only, with scripted models (docs/results.md). It makes no claim about task quality, cost or any model's behaviour. Outside these assumptions (side-effect tools, uncapped outputs, other budgets, very long conversations), the behaviour is the same code, with the consequences described in section 13.

## 15. Transcript tests

**What they are:** `tests/golden/scenarios.py` defines 32 scripted scenarios with synthetic data. Each scenario's transcript records:

- every request the model saw: messages, instructions, tool definitions and settings;
- every trace record;
- each run's output and full message history, or its error.

Timestamps, run ids, conversation ids, toolset ids and capability ids are left out.

**How they are used:** `tests/test_golden.py` runs the scenarios with `clm_context()` and requires transcripts identical to `tests/golden/data/`. `scripts/record_transcripts.py --check` does the same from the command line.

**How they were made:** the stored transcripts were recorded once with `scripts/record_transcripts.py`. They are a regression contract: they show that behaviour has not changed since they were recorded, not that the behaviour is correct by an independent standard (docs/results.md).

**What they cover:**

- **Requests and turns:** the request layout, turns and protection (including a repeated first request), every edit outcome and validation path, ordered operations (delete then re-add), argument coercion, a malformed edit that crashes the run, parallel calls, the status's step-count drift, conversations, structured output, agent instructions, images, non-ASCII text and `<` escaping.
- **The guard:** holding back (with realistic token density, including text denser than 1.5 characters per token), refetches, an edit freeing room, and an output that never fits.
- **Notices and endings:**
  - PRESSURE;
  - OVER LIMIT, with its re-arming within a turn and its per-turn reset;
  - both budget endings;
  - the step limit, the call cap and the finalize notice;
  - a raising tool.

**Rules:**

- The stored transcripts are never edited by hand, and never re-recorded to make a change pass. An approved behaviour change says how they change (AGENTS.md, "Changing behaviour").
- A run of the scenarios must be deterministic. Section 13.2 is why no scenario has two edits in one response, except `step_count_drift`. Its two edits are identical and rejected, so their order can't change the transcript.

**The segmented layouts** have no stored transcripts. `tests/test_request_layout.py` runs all 32 scenarios in each layout and compares them (section 13.17), and `tests/test_request_layout_anthropic.py` checks the request bodies from Pydantic AI's Anthropic adapter, over three user turns with early, late and rejected edits. The stored transcripts are all in the default layout, and stay identical.

**The frozen clock:** `run_scenario` runs every scenario inside `frozen_clock()`, which fixes Pydantic AI's clock at 2026-10-15T00:00:00.123456Z and restores it afterwards.

- **Why:** timestamps are left out of transcripts, but they still count towards the size estimates (section 13.14). A fixed time with a non-zero microsecond keeps every estimate independent of the wall clock.
- **How:** every message and part timestamp comes from `pydantic_ai._utils.now_utc`. The dataclass default factories and the `from ... import now_utc` imports bind that function at import time, so it isn't replaced. Instead, the `datetime` name it looks up on each call is.

## 16. Lineage

- **The design:** it follows the Context Language Models paper (arXiv 2609.37725), through clm-lib's working-context design (github.com/armalite/clm-lib), adapted to structured edits, parallel calls, an output guard with a conservative estimate, and protected turn entries.
- **Licence:** `context.py` and `instructions.py` derive from clm-lib (Apache-2.0), as NOTICE records. No code, prompt or notice text is taken from the paper's CC BY-NC reference repository.
