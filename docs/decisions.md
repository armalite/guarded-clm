# Decisions

Behaviour is defined in [SPEC.md](../SPEC.md); the roadmap is in [implementation-plan.md](implementation-plan.md).

- **Numbering:** P decisions concern packaging and verification. L decisions record how the library behaves by design. O items are open questions. Numbers are not consecutive: withdrawn items are not reused.
- **Changes:** a decision changes only through a new entry that supersedes it, and a decision that changes behaviour must say how the stored transcripts change (AGENTS.md).

## Packaging and verification

**P1 (2026-10-15). One implementation: the `ClmContext` capability.**
An earlier, unreleased core in this repository is retired:
- `GuardedConfig` and `GuardedContext`;
- its store, edits and turn lifecycle;
- its specification and decisions (D1 to D14, U1 to U11 below).

Its design differed from `ClmContext`. Among the differences:
- the reserved namespaces and runtime-owned protection;
- compact serialisation for the byte limit;
- strict edit parsing;
- materialisation at preparation time;
- structural limits enforced on runtime appends.

Only one implementation exists. There are no compatibility aliases for the preliminary API.

**P2 (2026-10-15). The names and compatibility strings are kept.**
- **Public names:** `ClmContext`, `ClmStepLimit` and `clm_context()`, under the `guarded_clm` import package.
- **Compatibility strings,** which are contracts and not branding:
  - the context format `clm-context/v1`;
  - the tool name `edit_context`;
  - the trace variant `clm_lite_v3_3` and the record types;
  - the capability id `clm_context`, the step limit's id `clm_step_limit` and the toolset id `clm_context_tools`.

**P4 (2026-10-15). Dependencies come from public PyPI, at the tested versions.**
- **Runtime:** `pydantic-ai-slim>=2.27.1,<2.28`, the tested minor.
- **The lockfile** is resolved from public PyPI only, at the versions the stored transcripts were recorded with.
- **A new Pydantic AI minor** needs the transcript tests to pass on it first. If they don't, adopting it is a behaviour decision.

**P5 (2026-10-15). The public transcript tests use fresh synthetic scenarios.**
32 scenarios cover the design's behaviours, with synthetic data. Their transcripts were recorded once and are a regression contract (L1); they don't establish correctness independently of the implementation.

**P6 (2026-10-16). Strict ratio learning, behind an option that is off by default; version 0.2.0.dev0.**
- **Why:** the shared estimator counts a request as appended whenever it is no smaller than the last one, and its new-text ratio only moves down (SPEC 8). In a long session with real usage, an edit removed about as much text as the step added. The request grew by a few dozen characters while the reported input rose by a few hundred tokens, so the ratio fell far below any real text density. A later over-limit check then estimated a request at several times the budget and ended the run ("over the budget itself"), although no reported input had come near the limit. The guard behaved correctly, because its own r learns only from requests that extend the last answered one (SPEC 13.16).
- **The option:** `strict_ratio_learning` on `ClmContext` and `clm_context()`. When it is on:
  - the new-text ratio learns only from a request that extends the last answered one, by the guard's test. An empty working context counts as a prefix, so that a run whose requests only append learns exactly as with the option off;
  - an observation below 0.5 characters per token is ignored.

  Nothing else changes: not the estimate methods, the overall ratio, the guard, notices, records or endings (SPEC 8).
- **Off by default** until evidence supports making it the default. With it off, the behaviour is exactly as before.
- **The stored transcripts are unchanged:** none of them sets the option, and all 32 pass as recorded (docs/results.md).
- **If it were the default,** 1 of the 32 would change, by an offline comparison that wrote nothing: `over_budget_ignored`, where the reported input jumps by 40,000 tokens. The backstop ignores that observation, so the run's final `context_limit` record and error estimate about 42,574 tokens instead of 95,668. The calls and the ending are the same. Making the option the default needs a new decision and new transcripts.
- **Version:** the distribution version and `guarded_clm.__version__` are both `0.2.0.dev0`, which closes O15.
- **Superseded by P7:** the option is removed, and its backstop is always on.

**P7 (2026-10-16). The backstop is always on; strict ratio learning is removed; version 0.2.0.dev1.** Supersedes P6.
- **The rule:** the shared estimator ignores any new-text observation below 0.5 characters per token (`MIN_PLAUSIBLE_NEW_TEXT_CHARS_PER_TOKEN`), so it never lowers the new-text ratio (SPEC 8 and 13.16). Everything else in the learning rule is as before P6: the size-based appended test, `MIN_NEW_TEXT_TOKENS`, keeping the lowest, and the starting ratio of 2.5.
- **Removed:** `strict_ratio_learning` on `ClmContext` and `clm_context()`, with its code paths, docs and tests. There is no deprecated alias. The output guard is unchanged.
- **Why:** strict ratio learning changed behaviour well beyond the case it was meant to fix. It also stopped the ratio learning from ordinary steps that grew after an edit at plausible ratios, and from the first call of each later turn, so estimates, notices and guard decisions would change in sessions that never had the defect. The backstop alone fixes that case, with the same figures as strict learning there, and leaves every other plausible observation as it was.
- **The stored transcripts:** all 32 were re-run, and exactly one changed: `over_budget_ignored`. Its script inflates the reported input by 40,000 tokens on purpose, which produces an observation below 0.5 that the backstop now ignores. That transcript was re-recorded, because:
  - every request and response the model saw is identical;
  - the guard decides the same (admit, then hold three times), and the held-back record is the same;
  - the run still ends at the same call, for the same reason: the next request's estimate is over the budget itself (`ContextLimitError`).

  The only differences are estimate figures derived from the learnt ratio. The guard checks' shared base and projection fall by 200 tokens (42,668 to 42,468, and 42,818 to 42,618); the held-back record's shared estimate goes from 109,268 to 42,602; and the final estimate, in the `context_limit` record and the error, goes from 95,668 to 42,574 tokens. The other 31 are unchanged.
- **Version:** `0.2.0.dev1`, in the distribution metadata and `guarded_clm.__version__`.

**P8 (2026-10-22). An opt-in segmented request layout, with an optional context cache point; version 0.2.0.dev2.**
- **Why:** the request carries the whole working context in one text block that ends with `<runtime_status>`, and the status changes on every request. Providers cache prompts at content-block boundaries: a cached prefix ends at the end of a block, and a changed block can't be reused, however much of its text is unchanged. So with full-conversation caching on, only the tools and the system prompt can be reused between requests.
- **The option:** `request_layout` on `ClmContext` and `clm_context()`.
  - **`"single"` (the default):** the existing layout, unchanged.
  - **`"segmented"`:** the user prompt part holds the same text as a list of items, which become separate content blocks:
    - the task head (`<task>…</task>`, then the opening `<working_context>` line);
    - one item per entry (its JSON line and newline);
    - the tail (the closing `</working_context>` line and `<runtime_status>`).

    Joined, the items are exactly the single layout's text.
- **The cache point:** `cache_point` (`"5m"`, `"1h"` or None; the default is None, and it applies to the segmented layout only). It adds a Pydantic AI `CachePoint` after the last entry, or after the task head when there are none: the last block that stays the same while only the status changes.
  - **Providers without prompt caching** drop the marker (Pydantic AI); the layout works with or without it.
  - **The library still has no provider-specific code:** `CachePoint` is Pydantic AI's provider-neutral marker.
- **The cache-boundary strategy:**
  - **Ordinary steps:** a step appends its entries after the existing ones, so the next request's prefix through the last unchanged entry is identical, and its cache point is two or three blocks after the previous one, within the provider's 20-block lookback.
  - **Status-only changes** come after the point, so they invalidate nothing before it.
  - **Edits:** an accepted edit invalidates from the first entry it changes or removes. The prefix before it can be read only when an earlier request's cache point sits at a block before it within the lookback, which is in practice an edit to recent entries.
  - **New turns:** a new user turn changes the task head (the current request is part of `<task>`), so it invalidates every entry block after it.
- **Compatibility:**
  - **The default mode is unchanged,** and all 32 stored transcripts stay identical.
  - **The segmented mode is a behaviour change, behind the option:**
    - the model sees the same characters, but as several content blocks, so requests are not byte-identical and responses may differ;
    - the shared estimator measures the serialized request, so the list form counts a few more characters (about three per item, plus the marker), and estimates, the status line and guard projections are slightly higher;
    - guard and notice decisions near a threshold can differ for that reason. The budget and the rules are unchanged.
  - **Unchanged in both modes:** the store, edits, protection, tool order, the guard's rules, the estimator's learning rules, recovery, the step and call limits, the instructions, trace records and the compatibility strings (P2).
- **Provider notes** (for Anthropic, from its prompt-caching documentation, read 2026-10-22):
  - **One message breakpoint:** with a host's automatic caching and its tool and system breakpoints, that makes four, the provider's maximum. Pydantic AI drops excess message breakpoints, oldest first.
  - **TTL order:** a longer TTL must come before a shorter one, so `cache_point` must not be longer than the host's earlier breakpoints.
  - **Minimum length:** a prefix shorter than the model's minimum is silently not cached.
- **Version:** `0.2.0.dev2`.

**P9 (2026-10-23). A second opt-in layout with the task after the working context, for reuse across turns; still 0.2.0.dev2.** Resolves O16.
- **Why:** in the segmented layout (P8) the `<task>` section comes first and holds the current request, so a new user turn changes the first block and invalidates every entry block after it. Only reuse within a turn survives.
- **The option:** `request_layout="segmented_task_last"`. It is the segmented layout with the same three sections in a different order:
  1. `<working_context>`, one block per entry;
  2. the optional `CachePoint` after the last entry;
  3. the tail, which holds the closing `</working_context>` line, then the whole `<task>` section (unchanged: the first request, and in later turns `Current request:` and the current one), then `<runtime_status>`.
- **Why this order, and not a split task:** the instructions describe exactly three sections and say the task "is never edited and always present". Moving the section whole keeps its content and needs no new framing. Keeping the first request at the top and only the current request at the end would need either a fourth section the instructions don't describe or a second `<task>`, for a small gain: the first request's tokens would be cacheable instead of re-sent in the tail. That variant is not implemented.
- **What stays at the front:** only stable text, namely the `<working_context>` opening line and the entry lines. The revision, entry count, step and call counters, size figures, notices, the closing delimiter and the current request are all in the tail, after the cache point.
- **The turn lifecycle is unchanged:** at a new turn the runtime appends the previous turn's answer (`t{N}.answer`) and, after the first turn, its request (`t{N}.request`) after the existing entries, as before. So the previous request's cache point, at its last entry, is still an unchanged prefix, a block or two before the new one.
- **Detecting the rebuilt request:** as first written, `absorb()` also took a user prompt starting with `<working_context>` for a rebuilt request in this layout. **Replaced by P10:** that discarded a genuine turn with that opening.
- **Model-visible difference:** the task and the current request come after the working context instead of before it. The section contents are the same, but the order is not, so the model may behave differently.
- **Sizes:** the same characters and items as the segmented layout, so the same estimates (P8's framing, counted).
- **What still invalidates:** an accepted edit, from the first entry it changes, as in P8. Text after that point can be reused only up to an entry where an earlier request's cache point ended, within the provider's 20-block lookback; after an early edit that is often only the first request's `<working_context>` head. A step that appends more than 20 blocks also passes the lookback. No extra breakpoints or cache bookkeeping are added for these cases.
- **Verified offline** (2026-10-23): the 32 transcripts are unchanged; in all 32 scenarios the task-last records and runs equal the segmented ones, with the `<task>` section moved; and request bodies from Pydantic AI's Anthropic adapter, over three user turns, show the previous marker surviving every append, status change and new turn, as listed in [results.md](results.md). It has not been checked against a live provider.
- **Kept:** the first segmented layout, `"segmented"`, is kept unchanged, for comparison. Neither is committed or used in a recorded run yet.

**P10 (2026-10-23). In the task-last layout, a rebuilt request is recognised by runtime-set metadata, not by its text; still 0.2.0.dev2.**
- **The defect (in P9 as first written, never released):** a later user turn that opened with `<working_context>` (for example a pasted example followed by a question) was taken for the rebuilt request. `absorb()` then started after it, so the turn was lost: the previous answer was not recorded, no new turn began, the model never saw the question and it never became `t{N}.request`. A turn opening with `<task>` was lost the same way. Shown through the public API with a scripted model.
- **The rule now, in this layout only:** `rebuild()` sets `ModelRequest.metadata = {"guarded_clm_rebuilt": True}` on the request it builds. Pydantic AI documents that field as available to the application and not sent to the model; it is kept in `result.all_messages()` and survives the history's JSON round trip. `absorb()` treats a request as rebuilt only when it carries that key, and never looks at the user's text. The caller's content is not changed.
- **Sizes:** the metadata is set after the request is measured, and the adapter doesn't send it, so estimates are unchanged (still equal to the segmented layout's). The scripted provider in the scenarios sizes the messages it is handed, metadata included, so the equivalence test measures them without it, as a provider receives them.
- **Not changed:** the single and segmented layouts keep the tested `<task>` prefix rule, so their transcripts stay identical. Their equivalent issue is O17. Notes (`[context manager]`) are unchanged in every layout: O18.
- **Limits:** a history that loses request metadata (a host that stores only text) is not supported in this layout, nor is changing layout within a conversation.

**P11 (2026-10-23). The intended default for the next release: `segmented_task_last` with a context cache point, after live validation.** Implemented by P13.
- **The decision:** the next released version is intended to default to `request_layout="segmented_task_last"` with `cache_point="5m"`. `"single"` and `"segmented"` stay available, set explicitly, for compatibility and experiments, and `cache_point=None` stays the way to send no marker.
- **Not yet:** this offline checkpoint keeps the current default, `"single"` with no cache point. The switch waits for a bounded live validation that shows real cache reuse within and across turns, and checks task behaviour with the task after the working context. Neither is established yet; the layout guarantees no cache hit.
- **When it switches:** a minor version bump, a release note, and the stored transcripts re-run under an explicit `request_layout="single"` so they keep testing the old layout unchanged.
- **Migration for callers, when the default switches:**
  - to keep today's behaviour exactly, pass `request_layout="single"` (no cache point);
  - to adopt the new default, pass back the history with its `ModelRequest.metadata` intact (`result.all_messages()` or its JSON form), keep one layout per conversation, and add any host notes with `add_context_note()` instead of a `[context manager]` text prefix (P12);
  - set the host cache settings recommended below, or turn the marker off with `cache_point=None`.
- **The recommended host settings** (Pydantic AI's Anthropic settings; the library doesn't set or override them): `anthropic_cache_tool_definitions` and `anthropic_cache_instructions`, and neither `anthropic_cache` nor `anthropic_cache_messages`. Those two put a breakpoint on the last block, which in this layout is the tail, and the tail changes in every request, so the entry written there is never read. That makes three breakpoints (the last tool, the system prompt, the last entry), within the limit of four, and the earlier two may have a longer or equal TTL than the context's marker. Checked offline in the adapter's request bodies (results.md).

**P12 (2026-10-23). Message origin by runtime metadata in the task-last layout: rebuilt requests, runtime notes and user turns; still 0.2.0.dev2.** Resolves O18 for that layout.
- **Why:** after P10, the task-last layout still classified a user prompt part by text in one case: a part starting with `[context manager]` was a note. So a genuine user turn with that opening was shown once in the status and never became a turn (O18).
- **What was checked first:** Pydantic AI 2.27.1's `UserPromptPart` has no metadata field; `ModelRequest.metadata` is the documented field "accessed programmatically by the application but is not sent to the LLM", already used by P10. No other public provenance mechanism for user prompt parts exists in that version. A separate harness's context-management arm was also read: it classifies notes by the same text prefix, so it has no mechanism to reuse.
- **The producers:** the library's only note producer is `ClmStepLimit`'s finalize notice, which appends a user prompt part to the request about to be sent: a tool-result request, or the user's own prompt request when a run starts with three or fewer steps left.
- **The rule:**
  - a new public helper, `add_context_note(request, text)`, appends `UserPromptPart(text)` to a `ModelRequest`, unchanged, and records its part index under `ModelRequest.metadata["guarded_clm_notes"]`, keeping any other metadata. `ClmStepLimit` uses it, in every layout;
  - in the task-last layout, a user prompt part is a rebuilt request if its request carries `guarded_clm_rebuilt` (P10), a note if its index is listed under `guarded_clm_notes`, and otherwise a user turn. Its text is never consulted, so `<task>`, `<working_context>` and `[context manager]` openings are ordinary user text there, neither escaped nor changed.
- **The older layouts:** their consumption keeps the tested prefix rules (O17, O18 there). The producer change only adds metadata to a request that `ClmContext` replaces with the rebuilt request before the model call, so neither the model nor the scripted provider sees it, and the transcripts stay identical.
- **Migration:** a host that injected its own note by starting a user prompt part with `[context manager]` must, in the task-last layout, call `add_context_note()` from its own capability before `ClmContext` runs. Otherwise its text is a user turn. No such caller is known; the old layouts still accept the prefix.
- **Persistence:** notes don't normally reach the history, because the rebuild replaces the request that carried them. Where a request with notes is kept (for example a run that ends before the rebuild), the indices survive the JSON round trip with the request.
- **Limits:** a capability placed between `ClmStepLimit` and `ClmContext` that reorders a request's parts would invalidate the indices. `clm_context()` keeps the two adjacent.

**P13 (2026-10-23). The default becomes `segmented_task_last` with a five-minute context cache point; version 0.3.0.dev0.** Implements P11, approved by the maintainer.
- **Why now:** the conditions P11 set were met. A downstream harness passed the options explicitly with the recommended host settings, and its bounded live checks (one development pair, then six sessions on previously used instances) showed the provider reading the cached working context on every ordinary call and every new-turn start, at the predicted boundaries, with task results no worse than the single layout's. The protocols and figures are in that harness's results repository; they are not repeated here.
- **The change is the defaults only:**
  - `request_layout` now defaults to `"segmented_task_last"`;
  - an omitted `cache_point` resolves to `"5m"` when the resolved layout is `"segmented_task_last"`, and to no marker for `"single"` and `"segmented"`;
  - an explicit `cache_point` is used as given: `None` sends no marker, and `"5m"` or `"1h"` with `"single"` still raises `ValueError`.
  - Request ordering, edits, protection, message origin, the guard, the estimator, recovery, limits and cache boundaries are unchanged: the defaults select the already tested layout.
- **Why `"segmented"` keeps no marker when it is omitted:** its marker was opt-in when it was tested (P8), and existing configurations that select it explicitly keep their behaviour.
- **This departs from the rule that behaviour changes ship off by default** (AGENTS.md). The maintainer approved the switch, as P11 planned it. The tested single layout stays available and its transcripts unchanged.
- **The stored transcripts** are now legacy-layout tests: `tests/test_golden.py` and `scripts/record_transcripts.py` select `request_layout="single"` with no cache point explicitly. Their expected files are unchanged.
- **Model settings stay with the host:** the library still sets nothing in them. The recommended Anthropic settings (P11) are documented, not applied.
- **Migration:**
  - **to keep the previous behaviour exactly,** pass `request_layout="single"`;
  - **to keep the new layout without a marker,** pass `cache_point=None`;
  - **with the new default:** keep one layout per conversation, pass back the history with its `ModelRequest.metadata` intact, and add host notes with `add_context_note()` (P12).
- **Version:** `0.3.0.dev0`, a minor bump for the changed default, in the distribution metadata and `guarded_clm.__version__`. No tag or release.

**P14 (2026-10-24). Release 0.3.0.**
- **What:** the version becomes `0.3.0` in the distribution metadata and `guarded_clm.__version__`, with the behaviour of 0.3.0.dev0 (P13) unchanged. The lockfile's own entry is refreshed; no dependency moves.
- **Release notes:** [CHANGELOG.md](../CHANGELOG.md), also included in the sdist and linked from the project URLs. They state the validation scope (offline checks here; live checks on one model and one workload in a downstream harness) and promise neither savings elsewhere nor unchanged model behaviour.
- **Tagging and publication** are the maintainer's: an annotated `v0.3.0` tag on the release commit. Nothing is uploaded from here (O13 still applies to the package name on public indexes).

## Library decisions

**L1. Exact behaviour is the contract.**
Every request the model sees, every tool result, every trace record and every ending is fixed by the stored transcripts. Internal refactors are fine only if every transcript stays identical.

**L2. A Pydantic AI capability first.**
The tested implementation is a Pydantic AI 2.27 capability, so the first version is one. A framework-agnostic core with adapters is possible later. That would be a restructure, and the transcripts would have to pass through each adapter.

**L3. The guard covers every tool.**
The guard runs a tool, then decides whether its output fits. Guarding only tools marked read-only would change behaviour. Tools with side effects are a documented limit instead (SPEC 13.1, O1).

**L5. Trace records keep their names.**
Every record carries `"variant": "clm_lite_v3_3"`, and the record types (`lite_edit`, `lite_held_back` and the rest) are unchanged, so tools reading them keep working.

**L7. Pinned to Pydantic AI 2.27** (see P4).

**L8. The host keeps its own limits and tool caps.**
- **Tool outputs:** the library doesn't cap them. The design assumes each tool caps its own output at about 8,000 characters.
- **Usage limits:** it doesn't set the run's `UsageLimits`. Pydantic AI's default `request_limit` is 50, below the default call cap of 144, so the host passes `UsageLimits(request_limit=CALL_CAP)`.

**L9. The capability id is configurable** (default `clm_context`).
Pydantic AI requires capability ids to be unique within an agent. `id=` on `ClmContext` and `clm_context()` lets the library sit beside another capability with the same id. `for_run` keeps the id, so the edit tool finds each run's copy.

## Open questions

Each one, if taken up, changes behaviour, so it needs a decision and new transcripts.

- **O1. Tools with side effects:** decide before running such a tool (from an output-size bound), or exempt them from parallel calls.
- **O2. Parallel edits:** two `edit_context` calls in one response race (SPEC 13.2). A concurrent call that ends the run can also race with an edit's trace record (results.md). Should edits be serialised?
- **O3. Protected-looking ids:** `add_note` with an id like `t3.answer` makes a protected note and leaves the real answer unprotected (`t3.answer~2`). Should such ids be rejected?
- **O4. State across processes:** the working context is in memory. Multi-replica services need sticky routing, or a persistence hook keyed by conversation.
- **O5. Guard and density state** are keyed by `id(session)` and never evicted. Move them onto the session?
- **O6. Long conversations:** protected history grows every turn. A digest beyond the last N turns is untested.
- **O7. Tool output cap:** offer an optional cap, off by default?
- **O8. Tracing:** OpenTelemetry spans from `on_record`, without context text by default?
- **O10. The status's task-step count against the enforced rule:** the status subtracts every `edit_context` call, but `ClmStepLimit` frees only a response whose first call is `edit_context`, so the count shown can drift, even below zero (SPEC 13.9). Should the status use `ClmStepLimit`'s count?
- **O11. Unhashable ids crash the run:** an edit whose `ids` holds objects passes the schema, then raises `TypeError` and ends `agent.run` (SPEC 13.11). Reject it as an invalid edit instead?
- **O12. An output that can never fit:** the guard asks the model to tidy and call again even when the output can't fit however much it tidies (SPEC 13.15). Should the model be told?
- **O13. Publishing:** the package name's availability on public indexes is unverified, and no release is authorised.
- **O14. The copyright line in NOTICE** names an individual. Keep it, or use the project handle? This is the maintainer's choice; the notice is unchanged until then.
- **O15. Version metadata:** closed by P6. Both versions are `0.2.0.dev0`.
- **O16 (closed by P9). Reuse across turns in the segmented layout:** a new user turn changes the `<task>` block, because it holds the current request, and that invalidates every entry block after it (P8). Moving the current request after the working context would let unchanged entries be reused across turns, but it changes the request format. Should it be a further option? **Answer:** yes, as a separate opt-in layout that moves the whole `<task>` section (P9); `"segmented"` and the default are unchanged.

- **O17 (open; the task-last layout doesn't have it, P10). A genuine turn that opens with `<task>` is lost in the single and segmented layouts.** Their tested rule takes any user prompt opening with `<task>\n` for the rebuilt request (SPEC 13.19), so such a turn is discarded as described in P10. Fixing it with the P10 metadata would change those layouts' estimates through the scripted provider's sizes, and so the stored transcripts. Should the old layouts get the same rule, with re-recorded transcripts?
- **O18 (closed by P12 for the task-last layout; open for the single and segmented layouts). A genuine turn that opens with `[context manager]` is shown once as a note:** it appears in the status, never becomes the current request or `t{N}.request`, and is gone from the next request (SPEC 13.19). In the task-last layout, notes are now recognised only by the metadata `add_context_note()` records (P12), so such a turn is an ordinary turn. The single and segmented layouts keep the tested prefix rule; changing it there would change what they record. Should they get the same rule, with re-recorded transcripts, together with O17?

## Superseded (the preliminary core, 2026-10-15)

Kept for the record; superseded by P1.

- **Preliminary decisions D1 to D14** covered a stdlib-only core, protection as runtime metadata, reserved turn and step namespaces, no renaming of duplicates, compact serialisation for limits, `fullmatch` for ids, materialisation at preparation, role mapping, the task anchor, no same-transaction re-add, pressure before the limit, lifecycle states, the trust boundary, and no load/restore. **All are superseded.** The current behaviour differs on most of them, and SPEC.md describes it.
- **Preliminary unresolved items U1 to U11** are settled by the current behaviour where it defines them, or carried as O items above:
  - **escaping:** every `<` is written as the JSON escape `\u003c` (SPEC 3);
  - **the call cap:** per run (SPEC 10);
  - **recovery:** re-arms within a turn and resets on a new turn (SPEC 9);
  - **the guard's held-output rules:** SPEC 7;
  - **the remaining items:** O1, O4 and O6.
