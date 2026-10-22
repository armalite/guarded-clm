# Architecture

Behaviour is defined in [SPEC.md](../SPEC.md). This document describes the structure.

## Modules

```
src/guarded_clm/
  __init__.py       public API: clm_context(), ClmContext, ClmStepLimit, add_context_note(), errors, constants
  capability.py     ClmContext: request rebuild, absorb (folding steps in), guard, edits, notices, hooks
  steps.py          ClmStepLimit: task steps (edit steps free), the call cap, the finalize notice;
                    add_context_note(), which marks a runtime note in the request's metadata
  context.py        entries, limits, validation, the revision store (derived from clm-lib)
  estimate.py       request-size measurement, the shared estimator and its calibration, conversation state
  instructions.py   the instruction text, as tested
  errors.py         ContextLimitError, BudgetExceededError, StepLimitError
tests/
  golden/scenarios.py   32 scripted scenarios (synthetic data; depends only on Pydantic AI)
  golden/data/*.json    the stored transcripts
  test_golden.py        the exact transcript check
  test_clm_context.py   readable unit tests
  test_paths.py         further path tests (estimator modes, serialisation, protection, overshoot, example)
  test_request_layout.py            the segmented layouts: items, marker, options, all 32 scenarios in each layout
  test_request_layout_anthropic.py  request bodies from Pydantic AI's Anthropic adapter, over one and three user turns
                                    (skipped without `anthropic`)
scripts/record_transcripts.py   records or checks the transcripts against any implementation (--package)
examples/basic_agent.py         an offline, runnable usage example
examples/segmented_layout.py    the segmented layouts and their cache point over two turns, offline
```

## How it plugs into Pydantic AI

`clm_context()` returns a `CombinedCapability` of `ClmStepLimit`, then `ClmContext`. Both are Pydantic AI capabilities, and their hooks run in that order around every model request.

```
agent.run(prompt, conversation_id, message_history)
  for_run: per-run copies; ClmContext's copy shares the per-conversation state (context store, estimator)
  each model request:
    ClmStepLimit.before_model_request   step/call limits; finalize notice appended to the request
    ClmContext.before_model_request     absorb: fold new responses, tool results, answers and turns into the store
                                        (the guard holds back outputs that don't fit); rebuild the request as
                                        system prompt + one user message <task>/<working_context>/<runtime_status>
                                        (one text item, or with a segmented request_layout one item per entry and
                                        an optional CachePoint after the last entry);
                                        PRESSURE / OVER LIMIT / ContextLimitError
    model call
    ClmStepLimit.after_model_request    count the call and the task step (free if the first call is edit_context)
    ClmContext.after_model_request      observe density r; calibrate the estimator; parallel-call and overshoot
                                        records; snapshot the entries before this response's tools run
  tools run (Pydantic AI's own execution, concurrently for sync tools); edit_context applies operations to the
  store and snapshots the result for the guard
```

## The request layouts

`prompt_part()` builds the request's user prompt part for each layout:
- **`"single"`:** `render_with()`'s text, one string;
- **`"segmented"`** (decisions P8): `render_items()`, the same text as items: the task head, one item per entry, the marker, the status tail;
- **`"segmented_task_last"`** (decisions P9): `render_items()` with the `<task>` section moved into the tail, so the items before the marker are only `<working_context>\n` and the entries.

The rebuild, the shared measure and the guard's character counts all call `prompt_part()`, so every estimate is taken on the representation that is sent. The `CachePoint` is Pydantic AI's provider-neutral marker: adapters with prompt caching turn it into a breakpoint on the block before it, and others drop it. The library itself has no provider-specific code. **Message origin.** `absorb()` classifies each user prompt part with `origin()`: the rebuilt request, a runtime note, or a user turn.
- **The task-last layout** uses provenance only (decisions P10, P12): `rebuild()` sets `ModelRequest.metadata = {"guarded_clm_rebuilt": True}` on the request after measuring it, and `add_context_note()` (in `steps.py`, used by `ClmStepLimit`'s finalize notice) lists a note's part index under `metadata["guarded_clm_notes"]`. The user's text never decides it. Pydantic AI keeps the field in the history and doesn't send it to the model.
- **The single and segmented layouts** keep the tested text rules: a `[context manager]` opening is a note, a `<task>` opening the rebuilt request (`part_text()` reads it from either form). The finalize notice is marked there too, but the rebuild replaces its request before the model call, so nothing they send or record changes.

**Why the task-last order reuses across turns:** a new turn appends the previous answer and request as entries (`t{N}.answer`, `t{N}.request`) and changes only the task text. In the task-last layout that text is after the marker, so the previous request's marked prefix is still unchanged. The store, the turn lifecycle, protection, edits, the guard and the estimator don't know which layout is used.

**Host cache settings** stay with the host: the library adds only the `CachePoint` and never reads or changes model settings. The recommended Anthropic settings for the task-last layout are in the README (decisions P11).

## State

- **Per conversation:** the context store and the size estimator live in the capability instance, in memory (at most 512 conversations).
- **Per session object:** the guard and density state.
- **Per run:** the call and step counters.

SPEC.md section 12 has the details and their consequences.

## Boundaries

- **The framework:** the library depends on Pydantic AI only, pinned to the tested minor. A provider-neutral core or a separate provider adapter is deferred ([implementation-plan.md](implementation-plan.md)). Either would need the same transcripts to pass through it.
- **Users of the library** depend on it; it depends on none of them.
