# Implementation plan

This is the roadmap.
- [SPEC.md](../SPEC.md) defines behaviour.
- [decisions.md](decisions.md) records the decisions and open questions.
- [architecture.md](architecture.md) describes the structure.
- The local, untracked `docs/HANDOFF.md` records the exact resume point.

This file links to those documents rather than repeating them.

**Course correction (2026-10-15).** guarded-clm publishes an existing, tested implementation of a Pydantic AI capability (`ClmContext`), ported faithfully. The earlier plan was superseded; it rebuilt the library independently in five chunks from a behavioural description. That plan's preliminary core (`GuardedConfig`, `GuardedContext`) differed from the tested behaviour and has been **retired**, not kept beside the port. See decision P1 in [decisions.md](decisions.md).

**Later steps are planned, not authorised.** Each needs an explicit instruction. Throughout, there are no provider API calls, spending, commits, pushes or publication unless separately authorised.

| # | Step | Status |
| --- | --- | --- |
| 1 | Faithful implementation port, and reconciliation of the existing core | **done** (2026-10-15) |
| 2 | Offline differential verification and packaging | **done** (2026-10-15) |

## 1. Faithful implementation port, and reconciliation of the existing core

- **Scope:** port the full library under the `guarded_clm` import package, keeping its behaviour identical:
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
- **Open decisions:** none for the port itself. The design's open questions are in [decisions.md](decisions.md).

## 2. Offline differential verification and packaging

- **Scope:**
  - an offline comparison of the port with an untouched reference copy, in separate processes;
  - a fresh public scenario suite with recorded transcripts;
  - unit tests;
  - a lockfile from public PyPI only;
  - building and inspecting the wheel and sdist;
  - a clean-environment install and example.
- **Depends on:** step 1.
- **Done when:**
  - every comparison is identical, or each difference is explained;
  - the public suite and the gates pass;
  - the artefacts contain no private metadata.

  The results and their limits are in [results.md](results.md).

## Scope of this plan

This plan covers library work only. Downstream projects that use the library keep their own integration plans,
experiments and results in their own repositories.

## Deferred, not part of this plan

- **A separate Anthropic adapter, or a provider-neutral core rewrite.** Either would need explicit equivalence requirements: the same transcripts through each adapter. Neither may change the ported behaviour.
- **Changes to the design's documented limitations.** Each needs a separately approved revision and new expected transcripts.
