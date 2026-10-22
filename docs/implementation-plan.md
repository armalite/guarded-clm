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
| 3 | An opt-in segmented request layout with an optional context cache point (decisions P8) | **done offline** (2026-10-22); not checked live |
| 4 | A second opt-in layout, the task after the working context, for reuse across turns (decisions P9) | **done offline** (2026-10-23); not checked live |
| 5 | Provenance-based turn detection in the task-last layout, and the recommended host cache settings (decisions P10, P11) | **done offline** (2026-10-23) |
| 5b | Message origin by runtime metadata for notes too, in the task-last layout (decisions P12) | **done offline** (2026-10-23) |
| 6 | Make `segmented_task_last` with a cache point the default (decisions P11) | **planned**; waits for the live validation |

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

## 3. An opt-in segmented request layout (decisions P8)

- **Scope:** `request_layout="segmented"` and `cache_point` on `ClmContext` and `clm_context()`: the same request text as several content items, one per entry, with an optional Pydantic AI `CachePoint` after the last entry, so a provider can reuse the unchanged working context between ordinary calls.
- **Depends on:** steps 1 and 2.
- **Done when:**
  - the default layout is unchanged, and all 32 stored transcripts pass;
  - the segmented layout is covered by focused tests, including request bodies from Pydantic AI's Anthropic adapter over an in-memory transport;
  - the docs describe the option, its cache boundaries and its differences.
- **Not in scope:** making it the default, reordering the request (O16), native conversation history, or any provider-specific code.

## 4. Reuse across turns: the task after the working context (decisions P9)

- **Scope:** `request_layout="segmented_task_last"`, which moves the `<task>` section (unchanged) after the working context, and the detection of its rebuilt request in `absorb()`. The `"single"` and `"segmented"` layouts stay as they are.
- **Depends on:** step 3.
- **Done when:**
  - all 32 stored transcripts pass;
  - the three layouts are compared on the scenarios;
  - request bodies from Pydantic AI's Anthropic adapter show, over at least three user turns, which cached boundary survives each transition;
  - the docs describe the order change.
- **Not in scope:** splitting the task section, native conversation history, or any provider-specific code.

## 5. Turn detection by provenance, and host cache settings (decisions P10, P11)

- **Scope:** in the task-last layout, recognise the rebuilt request by the `ModelRequest.metadata` the runtime sets, so a genuine user turn that opens like a rebuilt request is still a turn; document and check the recommended host cache settings. The other layouts are unchanged.
- **Done when:** a public-API regression shows such a turn reaching the request and being recorded and protected; rebuilt requests are still not ingested again; the adapter's request bodies show the recommended settings' breakpoints; all 32 transcripts pass.

## 5b. Message origin for runtime notes (decisions P12)

- **Scope:** `add_context_note()`, used by `ClmStepLimit`; the task-last layout classifies notes by its metadata, not by the `[context manager]` prefix. The single and segmented layouts keep their tested rules.
- **Done when:** public-API tests show genuine turns opening with `<task>`, `<working_context>` or `[context manager]` reaching the request and becoming one protected turn record; the finalize notice, on a tool-result request and on the user's own request, stays a note; the history survives in memory and as JSON; no metadata reaches the provider's request body; cache boundaries are unchanged; all 32 transcripts pass.

## 6. The default switch (decisions P11)

- **Scope:** make `request_layout="segmented_task_last"` with `cache_point="5m"` the default in the next released version. `"single"` and `"segmented"` stay available explicitly, and `cache_point=None` disables the marker.
- **Depends on, in order:**
  1. the maintainer commits steps 3 to 5;
  2. a downstream harness passes `request_layout` and `cache_point` explicitly, with the recommended host settings;
  3. a separately authorised, bounded live validation shows cache reads at the expected boundaries within and across turns, and checks task behaviour with the task after the working context.
- **Done when:** the default is switched with a minor version bump and a release note; the stored transcripts run under an explicit `request_layout="single"` and stay identical; README and SPEC describe the new default.
- **Caller migration** is listed in decisions P11: `request_layout="single"` keeps today's behaviour; the new default needs the history's metadata kept and host notes added with `add_context_note()`.
- **Not in scope until then:** switching the default, or describing live cache reuse or task behaviour as established.

## Scope of this plan

This plan covers library work only. Downstream projects that use the library keep their own integration plans,
experiments and results in their own repositories.

## Deferred, not part of this plan

- **A separate Anthropic adapter, or a provider-neutral core rewrite.** Either would need explicit equivalence requirements: the same transcripts through each adapter. Neither may change the current behaviour.
- **Changes to the design's documented limitations.** Each needs a separately approved revision and new expected transcripts.
