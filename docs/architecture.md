# Architecture

Behaviour is defined in [SPEC.md](../SPEC.md). This document describes the structure.

## Modules

```
src/guarded_clm/
  __init__.py       public API: clm_context(), ClmContext, ClmStepLimit, errors, constants
  capability.py     ClmContext: request rebuild, absorb (folding steps in), guard, edits, notices, hooks
  steps.py          ClmStepLimit: task steps (edit steps free), the call cap, the finalize notice
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
scripts/record_transcripts.py   records or checks the transcripts against any implementation (--package)
examples/basic_agent.py         an offline, runnable usage example
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
                                        system prompt + one user message <task>/<working_context>/<runtime_status>;
                                        PRESSURE / OVER LIMIT / ContextLimitError
    model call
    ClmStepLimit.after_model_request    count the call and the task step (free if the first call is edit_context)
    ClmContext.after_model_request      observe density r; calibrate the estimator; parallel-call and overshoot
                                        records; snapshot the entries before this response's tools run
  tools run (Pydantic AI's own execution, concurrently for sync tools); edit_context applies operations to the
  store and snapshots the result for the guard
```

## State

- **Per conversation:** the context store and the size estimator live in the capability instance, in memory (at most 512 conversations).
- **Per session object:** the guard and density state.
- **Per run:** the call and step counters.

SPEC.md section 12 has the details and their consequences.

## Boundaries

- **The framework:** the library depends on Pydantic AI only, pinned to the tested minor. A provider-neutral core or a separate provider adapter is deferred ([implementation-plan.md](implementation-plan.md)). Either would need the same transcripts to pass through it.
- **The experiment harness:** clm-lib, a separate repository, will import this library at a recorded version (plan step 3). It does not copy its logic.
