# Implementation plan

This is the roadmap: bounded chunks in order, each with its scope, dependencies, completion checks, status and open decisions. Other documents hold the rest:
- [SPEC.md](../SPEC.md) defines behaviour;
- [decisions.md](decisions.md) records the decisions taken and the open questions;
- [architecture.md](architecture.md) describes the structure;
- the local, untracked `docs/HANDOFF.md` records the exact resume point.

This file links to them rather than repeating them.

**Later chunks are planned, not authorised.** Each starts only on an explicit instruction, and a chunk may be split further to fit one working session. Throughout, there are no provider API calls, spending, commits, pushes or publication unless separately authorised.

| # | Chunk | Status |
| --- | --- | --- |
| 1 | Repository setup, configuration, context store, transactional edits, protected turns | **done** (2026-10-15) |
| 2 | Request builder, size estimation, output guard and recovery | planned |
| 3 | Anthropic adapter with scripted end-to-end verification | planned |
| 4 | Pydantic AI capability with offline history and execution-order verification | planned |
| 5 | clm-lib integration, pinned library provenance, offline experiment readiness | planned |

## 1. Repository setup, configuration, context store, transactional edits, protected turns

- **Scope:**
  - the project layout, tooling and agent instructions;
  - the validated configuration (`GuardedConfig`);
  - entries, edit operations, snapshots and edit results;
  - the authoritative `ContextStore`, with deterministic serialisation and structural limits;
  - transactional `delete` / `replace` / `add_note`;
  - runtime-owned protection and the reserved ID namespaces;
  - the turn lifecycle (`GuardedContext`), with an idempotent request-preparation hook;
  - a model-free example.

  See SPEC §1–§8.
- **Depends on:** nothing.
- **Done when:**
  - ruff, format, mypy and pytest are clean;
  - the package builds, and the wheel installs in a clean environment and runs the example;
  - the docs and handoffs are updated;
  - clm-lib and clm-lib-test-results have pointers only.
- **Status:** done (2026-10-15). The checks are in [results.md](results.md).
- **Open decisions:** none block this chunk. Items it exposed are in [decisions.md](decisions.md) under "Unresolved".

## 2. Request builder, size estimation, output guard and recovery

If this does not fit one session, it can be split into 2a (request builder and the shared estimator), 2b (the output guard) and 2c (the recovery state machine).

- **Scope:**
  - building the request (system prompt, plus one reconstructed user message with the task, working context and runtime status);
  - runtime step entries (`sN.act`, `sN.obs`, `sN.rcpt`);
  - the shared estimator, anchored to reported input usage;
  - the conservative output guard;
  - pressure and the recovery state machine;
  - task-step and call counters, including the specified free-step rule.

  See SPEC §9 (planned).
- **Depends on:** chunk 1. The request builder will call the `GuardedContext.prepare_request()` hook.
- **Done when:**
  - the builder's output is deterministic and covered by tests;
  - the estimator is tested only against recorded or scripted usage that is labelled as such;
  - every guard decision rule has a test, including held-output ordering;
  - the recovery transitions are tested;
  - nothing calls a provider.
- **Open decisions (must be settled before or during this chunk):**
  - the malformed escaping instruction;
  - the accounting meaning of the 144-call cap;
  - re-arming recovery after a return below the limit, checked against clm-lib's existing behaviour;
  - placeholder overflow, and an output too large on its own;
  - whether later calls run after an output is held;
  - mapping provider usage, including cache reads and writes, to append-only observations;
  - exhaustion when protected history fills the budget.

  All are listed in [decisions.md](decisions.md).

## 3. Anthropic adapter with scripted end-to-end verification

- **Scope:**
  - an optional adapter, kept out of the core import path;
  - tool definitions for `edit_context` and read-only task tools;
  - mapping responses to calls processed in order;
  - usage mapping;
  - end-to-end tests with a scripted client.
- **Depends on:** chunk 2.
- **Done when:**
  - scripted end-to-end runs pass;
  - the core still imports without the Anthropic SDK;
  - live compatibility is explicitly **not** claimed.
- **Open decisions:**
  - provider-required content blocks (for example, thinking blocks) in reconstructed requests;
  - support for tools with side effects (future work).

## 4. Pydantic AI capability with offline history and execution-order verification

- **Scope:**
  - an optional Pydantic AI integration exposing the same behaviour;
  - offline tests of message-history handling and in-order execution of several calls.
- **Depends on:** chunks 2 and 3, for the shared request and tool semantics.
- **Done when:**
  - offline tests pass with a test model;
  - the core still imports without Pydantic AI.
- **Open decisions:** the exact integration point in Pydantic AI, to be checked against its official documentation at that time.

## 5. clm-lib integration, pinned library provenance and offline experiment readiness

- **Scope:**
  - clm-lib imports a pinned guarded-clm (version, or commit plus hash) for programme 015's lite arms;
  - run provenance records the library version;
  - an offline proof through clm-lib's real runner and sandbox.
- **Depends on:** chunks 2 and 3. Chunk 4 is not required for the harness.
- **Done when:**
  - clm-lib's offline tests pass using the imported library, with no duplicate implementation in clm-lib;
  - provenance is recorded;
  - experiment readiness is documented.
  - Any live run needs a separate authorisation.
- **Open decisions:**
  - how the 015 lite arms (`clm_lite_v2`, `clm_lite_v3_1`) map onto library configuration;
  - the pinning mechanism (a local path, a git commit or a release).
