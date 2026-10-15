# Architecture

Behaviour is defined in [SPEC.md](../SPEC.md). This document describes structure and boundaries.

## Core versus adapters

```
            planned adapters (optional extras, separate import paths)
   ┌───────────────────────────┐   ┌───────────────────────────────┐
   │ Anthropic adapter (ch. 3) │   │ Pydantic AI capability (ch. 4)│
   └─────────────┬─────────────┘   └───────────────┬───────────────┘
                 │  tool calls in order, usage     │
                 ▼                                 ▼
   ┌────────────────────────────────────────────────────────────────┐
   │ guarded_clm core (stdlib only)                                 │
   │  planned (ch. 2): request builder · estimator · output guard · │
   │                   recovery state machine · counters            │
   │  implemented (ch. 1):                                          │
   │    GuardedContext  turn lifecycle, prepare_request() hook      │
   │    ContextStore    entries, revisions, transactions, limits    │
   │    Entry / edits   validation, canonical serialisation         │
   │    GuardedConfig   validated settings                          │
   └────────────────────────────────────────────────────────────────┘
                 ▲
                 │ imports a pinned version (ch. 5)
   ┌─────────────┴──────────────┐
   │ clm-lib experiment harness │  (separate repository; no duplicate implementation)
   └────────────────────────────┘
```

| Module | Responsibility |
| --- | --- |
| `config.py` | `GuardedConfig`: validation of types and ranges |
| `entries.py` | `Entry`, the ID and role rules, reserved namespaces, structural limits, canonical serialisation |
| `edits.py` | the `Delete` / `Replace` / `AddNote` operations, strict parsing of their JSON form, `EditResult` and receipts |
| `store.py` | `ContextStore` (the authoritative state and revisions) and `Snapshot` |
| `context.py` | `GuardedContext`: turn states, the task anchor, the current request, record materialisation |

## Store and transaction boundaries

- **Immutable state:** the live state is an immutable tuple of frozen `Entry` objects plus a revision number. A `Snapshot` shares that tuple, so it is cheap and cannot be used to change the live state. `to_dict()` returns copies.
- **One path for every change:** all changes go through a single commit path that validates the complete candidate state. Model transactions (`apply_edits`) build a candidate list, apply the operations in order, check the structural limits once on the result, and commit at most one revision. Host appends and lifecycle materialisation use the same checks.
- **Errors split by source:** model-influenced input yields result objects (`EditResult`, `ChangeResult`, `PrepareResult`), never exceptions. Host programming errors (lifecycle misuse, an invalid configuration) raise exceptions.
- **Trust:** host APIs are trusted and validate their input; they are not a sandbox (SPEC §7).

## Future request-building flow (planned, chunk 2)

```
start_turn(request) ─► prepare_request()  (materialise turn records, idempotent)
                        │
                        ▼
                  build request: system prompt + one user message
                  [task | working_context (canonical JSON) | runtime_status (+ current request)]
                        │
                        ▼
                  adapter sends; response tool calls processed in order:
                    edit_context → apply_edits (never held)
                    task tools   → output guard (estimate; hold or deliver)
                        │
                        ▼
                  append sN.act / sN.obs / sN.rcpt; update estimator from usage;
                  pressure / recovery state; next request or submit_answer
```

## The experiment harness

clm-lib, a separate repository, will import a pinned guarded-clm for its programme-015 lite arms (plan chunk 5) and record the library version in run provenance. clm-lib does not contain a copy of this logic.
