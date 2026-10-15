# Implementation plan

This is the roadmap.
- [SPEC.md](../SPEC.md) defines behaviour.
- [decisions.md](decisions.md) records the decisions and open questions.
- [architecture.md](architecture.md) describes the structure.
- The local, untracked `docs/HANDOFF.md` records the exact resume point.

This file links to those documents rather than repeating them.

**Current state (2026-10-15).** guarded-clm provides the `ClmContext` Pydantic AI capability. An earlier five-chunk plan, and the unreleased preliminary core it produced (`GuardedConfig`, `GuardedContext`), had a different design. Both are **retired**, and the preliminary core is not kept alongside the current one (decision P1 in [decisions.md](decisions.md)).

**Later steps are planned, not authorised.** Each needs an explicit instruction. Throughout, there are no provider API calls, spending, commits, pushes or publication unless separately authorised.

| # | Step | Status |
| --- | --- | --- |
| 1 | The `ClmContext` implementation, replacing the preliminary core | **done** (2026-10-15) |
| 2 | Offline verification and packaging | **done** (2026-10-15) |

## 1. The `ClmContext` implementation, replacing the preliminary core

- **Scope:** the full library under the `guarded_clm` import package:
  - the entries, store, revisions, appends and collision handling;
  - validation and serialisation;
  - edit operations and receipts;
  - turn detection, answer capture and protection;
  - request reconstruction, the instructions and the edit tool;
  - the shared and the conservative estimators, the guard, edit snapshots, held outputs and refetches;
  - pressure and recovery;
  - step and call limits, and notices;
  - conversation state, trace records and exceptions;
  - the `clm_context()` factory and the Pydantic AI integration.

  Then retire the preliminary core, with its tests and docs.
- **Depends on:** nothing.
- **Done when:**
  - one implementation exists;
  - the public names are kept (`ClmContext`, `ClmStepLimit`, `clm_context`);
  - compatibility strings are unchanged: the context format, the tool name, the trace variant and record types.
- **Open decisions:** the design's open questions are in [decisions.md](decisions.md).

## 2. Offline verification and packaging

- **Scope:**
  - a public synthetic scenario suite with recorded transcripts;
  - unit tests;
  - a lockfile from public PyPI only;
  - building and inspecting the wheel and sdist;
  - a clean-environment install and example.
- **Depends on:** step 1.
- **Done when:**
  - the transcript suite passes, and every recorded difference is explained;
  - the public suite and the gates pass;
  - the artefacts contain no private metadata.

  The results and their limits are in [results.md](results.md).

## Scope of this plan

This plan covers library work only. Downstream projects that use the library keep their own integration plans,
experiments and results in their own repositories.

## Deferred, not part of this plan

- **A separate Anthropic adapter, or a provider-neutral core rewrite.** Either would need explicit equivalence requirements: the same transcripts through each adapter. Neither may change the current behaviour.
- **Changes to the design's documented limitations.** Each needs a separately approved revision and new expected transcripts.
