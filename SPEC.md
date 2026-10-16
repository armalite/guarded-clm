# guarded-clm specification: `ClmContext`

**Status:** 0.1.0.dev0 (15 October 2026). It describes the implemented behaviour of `ClmContext` and `ClmStepLimit`. The trace records name the design `clm_lite_v3_3`.

**This document is normative.** The library must behave exactly as described here. `tests/test_golden.py` checks that against stored transcripts (section 15). Where this text and the transcripts disagree, the transcripts win, and this text is the bug.

**Version history:**
- **0.1.0.dev0 (2026-10-15):** the first specification of `ClmContext`. It replaces an earlier, unreleased core in this repository, which had a different design (docs/decisions.md, P1).

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

- **`clm_context(*, budget_tokens=32_000, max_steps=60, call_cap=None, end_over_budget=False, max_output_chars=8_000, limits=None, on_record=None, id="clm_context", strict_ratio_learning=False)`** returns a `CombinedCapability` of `ClmStepLimit(max_steps, call_cap=cap)`, then `ClmContext(...)`, in that order. `cap` is `call_cap`, or `2 × max_steps + 24` (144 by default). Pydantic AI flattens a combined capability into its leaves, so this is the same as listing the two.
- **`ClmContext(*, budget_tokens=32_000, end_over_budget=False, max_output_chars=8_000, max_steps=60, call_cap=144, limits=None, on_record=None, id="clm_context", strict_ratio_learning=False)`:**
  - `max_steps` and `call_cap` are only shown in the runtime status. `ClmStepLimit` enforces them.
  - `max_output_chars` caps the `edit_context` tool's result (section 6.4).
  - `limits` is a `ContextLimits(max_file_bytes=256_000, max_entries=200, max_body_chars=24_000, max_total_chars=120_000)`.
  - `strict_ratio_learning` restricts how the shared estimator learns its new-text ratio (section 8, decisions P6). Off by default, as tested.
  - `id` is the capability id (L9). Pydantic AI requires ids to be unique within an agent's capabilities, so pass another one when a different capability already uses `clm_context`. `for_run` gives each run's copy the same id, and the edit tool finds that copy by it.
- **`ClmStepLimit(max_steps=60, *, call_cap=None, finalize=True)`:** section 10.
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
  - the notes found in the request: user prompt parts starting with `[context manager]`, such as the finalize notice;
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

The new messages are those after the last request whose user prompt starts with `<task>\n`. They are taken in order:

- **A response with tool calls:**
  1. `step += 1`.
  2. The action entry is built.
  3. The tool results are taken from the next request (`ToolReturnPart` and `RetryPromptPart`): by call id, in the order the calls were listed, then any unmatched results.
  4. The results pass through the guard.
  5. `sN.act`, `sN.obs` and `sN.rcpt` (if a receipt is pending) are appended as one revision.
- **A response without tool calls:** its text becomes `t{turn}.answer`, if non-empty.
- **A request's user prompt parts:**
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
- **The new-text ratio** (from 2.5) keeps the lowest `added chars / added tokens`, for additions of at least 200 tokens, from a request counted as appended (no smaller than the last one).

**Strict ratio learning** (`strict_ratio_learning=True`, decisions P6; off by default). Only the new-text ratio's update changes:

1. **Only extending requests teach it.** The request must extend the last answered one: the same task, and the last answered request's entry lines a prefix of this one's. This is the guard's own test (section 7), except that an empty working context counts as a prefix of any other. A request that grew in size without extending the last one (for example, after an edit that removed about as much as the step added) doesn't update the new-text ratio.
2. **A backstop:** an observation below 0.5 characters per token is ignored as implausible.

The estimate methods, the overall ratio, the guard, notices, trace records and endings are unchanged. On a run where every request extends the last answered one, learning is identical to the default. The first call of a later turn changes the task, so it doesn't teach the new-text ratio under strict learning.

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
3. If `left = min(max_steps − steps, call_cap − calls)` is at most 3, and the last message is a request, it appends the user prompt part `[context manager] {left} step(s) left: finish now and give your final answer.` to that request. ClmContext then shows it as a notice.

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

16. **One non-extending step can collapse the new-text ratio** (the default; `strict_ratio_learning` is off).
    - **Why:** a request counts as appended whenever it is no smaller than the last one (section 8), and the new-text ratio only ever moves down.
    - **What happens:** when an edit removes about as much text as the step adds, the request can grow by a few characters while the reported input rises by hundreds of tokens. The new-text ratio then drops far below any real density and stays there.
    - **What it means:** later anchored estimates are many times too large. The over-limit check can end the run ("over the budget itself") although no reported input came near the budget. The guard is unaffected: its own r learns only from extending requests (section 7).
    - **An example, with synthetic numbers:** a request that grew by 40 characters while the reported input rose by 250 tokens sets the ratio to 0.16. A later request adding 30,000 characters is then estimated at about 195,750 tokens, against a limit of 29,952.
    - **The option:** `strict_ratio_learning=True` (section 8) learns only from extending requests and ignores implausible observations (decisions P6).

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

**The frozen clock:** `run_scenario` runs every scenario inside `frozen_clock()`, which fixes Pydantic AI's clock at 2026-10-15T00:00:00.123456Z and restores it afterwards.

- **Why:** timestamps are left out of transcripts, but they still count towards the size estimates (section 13.14). A fixed time with a non-zero microsecond keeps every estimate independent of the wall clock.
- **How:** every message and part timestamp comes from `pydantic_ai._utils.now_utc`. The dataclass default factories and the `from ... import now_utc` imports bind that function at import time, so it isn't replaced. Instead, the `datetime` name it looks up on each call is.

## 16. Lineage

- **The design:** it follows the Context Language Models paper (arXiv 2609.37725), through clm-lib's working-context design (github.com/armalite/clm-lib), adapted to structured edits, parallel calls, an output guard with a conservative estimate, and protected turn entries.
- **Licence:** `context.py` and `instructions.py` derive from clm-lib (Apache-2.0), as NOTICE records. No code, prompt or notice text is taken from the paper's CC BY-NC reference repository.
