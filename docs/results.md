# Results

**Offline verification only.** There are no model runs, no provider calls and no performance findings. Nothing here measures task quality, cost or speed with a real model.

## 2026-10-23: the cache-friendly layout becomes the default (0.3.0.dev0)

Decision P13 (SPEC 2, 3, 3.2 and 15; implements P11). Offline only: scripted models, and request bodies from Pydantic AI's Anthropic adapter over an in-memory transport. No provider or token-counting calls. (The live checks that led to the decision were run by a downstream harness and are recorded in its results repository.)

- **The defaults:** `request_layout="segmented_task_last"`; an omitted `cache_point` resolves to `"5m"` for that layout and to no marker for `"single"` and `"segmented"`; an explicit value, None included, is used as given.
- **Omitted equals explicit:**
  - in all 32 scenarios, the records, runs, model inputs and request items with both options omitted equal those with `"segmented_task_last"` and `"5m"` stated;
  - through the Anthropic adapter, three turns with early, late and rejected edits send identical request bodies, with the recommended host settings, with automatic caching set by the host, and with caching off.
- **The host's settings stay its own:** with automatic caching set, the top-level breakpoint is still sent; without tool or instruction caching, those blocks carry no marker.
- **The other options:**
  - explicit `"single"` sends the original one-block request, with no marker and no metadata on the wire;
  - explicit `"segmented"` keeps its opt-in marker;
  - explicit `cache_point=None` sends no library marker;
  - a marker with `"single"`, an unknown layout and an unknown TTL raise `ValueError`.
- **Message origin:** the P10 and P12 tests pass with the layouts stated, and the readable tests now run in the default layout, the finalize notice included.
- **The stored transcripts:** all 32 pass unchanged with `"single"` selected explicitly in `tests/test_golden.py` and `scripts/record_transcripts.py`. No expected file changed.
- **The gates:**
  - `make check` passes: ruff, pyright 0 errors, pytest 94 passed and 1 skipped;
  - `make transcripts-check` passes;
  - the 21 adapter tests (115 in the full suite) pass in an environment with `anthropic` 0.125.0;
  - `uv build` gives 0.3.0.dev0, with LICENSE and NOTICE.

## 2026-10-23 (last): runtime notes by provenance in the task-last layout (0.2.0.dev2)

Decision P12 (SPEC 2, 3, 4.2, 10, 13.18 and 13.19). Offline only: scripted models, and the adapter's request bodies over an in-memory transport with a placeholder key. No provider or token-counting calls.

- **What was inspected first:**
  - Pydantic AI 2.27.1's `UserPromptPart` has no metadata field; `ModelRequest.metadata` (used by P10) is the documented application-only field;
  - a separate harness's context-management arm classifies notes by the same text prefix, so it had nothing to reuse.
- **The library's note producer** is `ClmStepLimit`'s finalize notice. With `max_steps=3`, it lands on the user's own prompt request at each run's first call and on tool-result requests after that.
- **Through the public API, in the task-last layout** (three turns with a scripted model, history passed in memory and as JSON):
  - later turns opening with `<working_context>`, `<task>` or `[context manager]`, each a pasted fragment followed by a question:
    - the exact text reaches the next request as the current request, not in the status;
    - the previous answer is recorded once;
    - at the next turn the text is exactly one protected `t2.request`;
  - **the finalize notice** is in the status of all six requests, never in the entries or the task, on both kinds of request. The requests equal the segmented layout's with the task section moved, so the library's notes are handled exactly as the tested prefix rule handles them there;
  - **notes don't persist:** each turn's history holds one marked rebuilt request and no note metadata;
  - **host notes:** a host capability's note added with `add_context_note()` is a note in all three layouts. In the task-last layout, an unmarked `[context manager]` part from a host is user text and starts a turn (the documented migration case); in the segmented layout it is still a note;
  - **`add_context_note()` metadata** survives the JSON round trip, keeps other metadata, and classifies as a note.
- **The older layouts, unchanged:** a `<task>` opening still loses the turn, and a `[context manager]` opening is still shown once as a note (SPEC 13.19, O17 and O18 there).
- **Request bodies (Anthropic adapter)**, three turns with the prefixed questions, the finalize notice on and the recommended settings:
  - the same reusable boundary at every call as the plain run;
  - every body's status carries the notice;
  - the pasted `<working_context>` question is one protected `t2.request`;
  - no body contains any `guarded_clm` metadata key.
- **Checks of the tests:**
  - with the `[context manager]` prefix rule put back in the task-last layout, the note-opening turn cases and the host migration test fail;
  - with `ClmStepLimit` appending its notice unmarked, both finalize-notice tests fail.
- **The gates:**
  - `make check` passes: ruff, pyright 0 errors, pytest 92 passed and 1 skipped, with 32 in `test_request_layout.py`;
  - `make transcripts-check` passes: all 32 are identical, including the step-limit scenario whose notice is now added with `add_context_note()`;
  - in an environment with `anthropic` 0.125.0, the 17 adapter tests pass.

## 2026-10-23 (later): turn detection by provenance, and the recommended host settings (0.2.0.dev2)

Decisions P10 and P11 (SPEC 3.2, 4.2, 13.18 and 13.19). Offline only, as in the entry below: scripted models, and the adapter's request bodies over an in-memory transport with a placeholder key. No provider or token-counting calls.

- **The defect, shown through the public API** (three turns, a scripted model; the second turn is a pasted `<working_context>` block followed by a question):
  - **before the fix,** in the task-last layout, the second turn was lost: its request still showed the first question, `t1.answer` was missing, and the question never appeared in any request or as `t2.request`. The same happened for a pasted block that opened with `<task>`;
  - **after the fix,** the question reaches the second request verbatim as the current request, `t1.answer` is recorded once, and at the third turn the question is the protected `t2.request`, once. This holds with the history passed in memory and after a JSON round trip.
- **The same check in the older layouts, unchanged:** `"single"` and `"segmented"` handle a `<working_context>` opening correctly, but lose a turn that opens with `<task>`, as their tested rule says (SPEC 13.19, open question O17). In every layout, a turn that opens with `[context manager]` is shown once as a status note and never becomes a turn (O18). Neither was changed.
- **Checks of the tests:**
  - with the old prefix rule restored in-test, all four new regression cases fail;
  - with rebuilt requests never recognised, those four and the not-ingested-again test fail.
- **Sizes:** the metadata is set after measuring and isn't sent, so in all 32 scenarios the task-last records and runs still equal the segmented ones. The scripted provider sizes the messages it is handed, metadata included, so that comparison sizes them as a provider receives them: without the metadata.
- **The recommended host settings** (`anthropic_cache_tool_definitions` and `anthropic_cache_instructions`, no `anthropic_cache`), over the three-turn conversation, at TTLs of `5m` and `1h` for the first two with a `5m` cache point:
  - every body has exactly three breakpoints: the last tool, the system prompt and the last entry, in that order, with a valid TTL order;
  - no top-level `cache_control` and no marker on the tail;
  - the reusable boundary at every call is the same as with automatic caching on (the table below);
  - the rebuilt-request metadata never appears in a body.
- **Why automatic caching is left out:** with it on, every request's breakpoint is the tail, and all ten tails differ, so the entry it writes there can't be read. In this conversation the tail was 305 to 352 characters (the task section and the status) out of 324 to 3,334 in the user message. Leaving it out avoids writing those characters at the cache-write rate. The size depends on the task text's length.
- **The gates:** `make check` passes (ruff, pyright 0 errors, pytest 85 passed and 1 skipped: 25 in `test_request_layout.py`); `make transcripts-check` passes with all 32 identical; in an environment with `anthropic` 0.125.0, the 16 adapter tests pass (the full suite: 101 passed).
- **Not established:** real cache reads, how they compare across the layouts, and the model's task behaviour with the task after the working context. The intended default switch (P11) waits for those.

## 2026-10-23: the task-last segmented layout, for reuse across turns (0.2.0.dev2)

Decision P9 (SPEC 3.2, 4.2, 8, 13.17 and 13.18). Offline only: scripted models, and request bodies from Pydantic AI's Anthropic adapter over an in-memory transport with a placeholder key and no network. No provider calls, including token counting.

- **`make check` passes:** ruff, pyright (0 errors) and pytest, 78 passed and 1 skipped: the 70 from the entry below, plus 8 more in `test_request_layout.py` (18 in all). The skipped module needs `anthropic`, as before.
- **The adapter checks:** the 13 tests in `test_request_layout_anthropic.py` pass in an environment with `pydantic-ai-slim` 2.27.1 and `anthropic` 0.125.0, with this source installed editable (the full suite there: 91 passed).
- **`make transcripts-check` passes:** all 32 stored transcripts are identical. None was re-recorded.
- **All 32 scenarios in the three layouts** (`test_request_layout.py`):
  - **task-last against segmented:** in every scenario the trace records and the runs are equal, and each rebuilt text is the segmented text with the `<task>` section moved. So the estimates, PRESSURE, held-back notes, endings, holds, refetches and recovery are all the same as the segmented layout's;
  - **task-last against single:** the same decisions, and the figure differences listed below for the segmented layout (9 to 69 tokens per estimate, median 22);
  - **a check of the tests:** with `absorb()`'s old detection (the `<task>` opening only), the task-last runs no longer match, so the tests catch a detection regression.
- **The request bodies over three user turns** (10 calls; turn 1: two lookups and an answer; turn 2: a lookup, an early edit deleting `s1.obs`, an answer; turn 3: two lookups, a late edit deleting the newest observation `s6.obs`, an answer), with the host's automatic, tool and system caching on and a five-minute cache point. Each row is the earlier cache boundary that is an unchanged, eligible prefix of that call's request (an exact block prefix ending at a block an earlier request marked, within the 20-block lookback):

  | Call | Transition | `"segmented_task_last"` | `"segmented"` | `"single"` |
  | --- | --- | --- | --- | --- |
  | 1 | an append (first lookup) | `<working_context>` head (call 0's marker) | the task head | tools and system |
  | 2 | an append | call 1's last entry, `s1.obs` | the same | tools and system |
  | 3 | **a new turn** | call 2's last entry, `s2.obs` | tools and system (the task block changed) | tools and system |
  | 4 | an append | call 3's last entry, `t1.answer` | the same | tools and system |
  | 5 | **an early edit** (`s1.obs` deleted) | the `<working_context>` head only | tools and system | tools and system |
  | 6 | **a new turn** | call 5's last entry, `s4.rcpt` | tools and system | tools and system |
  | 7 | an append | call 6's last entry, `t2.request` | the same | tools and system |
  | 8 | an append | call 7's last entry, `s5.obs` | the same | tools and system |
  | 9 | **a late edit** (`s6.obs` deleted) | call 7's last entry, `s5.obs` | the same | tools and system |

  - **Across turns:** a new turn appends `t{N}.answer` (and from turn 3 `t{N}.request`) after the entries, so the previous marker is one or two blocks back. In call 6, for example, the request is `<working_context>`, the nine entries from call 5, then `t2.answer` and `t2.request` (marked), then the tail with the task (now with `Current request:` and the third question) and the status.
  - **After the early edit (call 5):** the unchanged text runs on through `s1.act`, but no earlier request ended its entries there, so no entry can be read there. That is unchanged text with no reusable entry, not an invalidation by the layout.
  - **After the late edit (call 9):** the deleted `s6.obs` came after call 7's marker, so that boundary survives. Call 8's marker, at `s6.obs`, does not.
  - **Rejected edits** (a protected id, or an unknown id, in turn 2): nothing is removed and the next request reuses the previous marker.
  - **The turn records:** `t1.answer`, `t2.request` and `t2.answer` each appear once per request, with `"protected": true`; the first question appears only in the task section, the second only in the task section until turn 3 and then only as `t2.request`; `s1.obs` and `s6.obs` are absent from every request after their deletes.
  - **Markers:** at most four breakpoints in every body; the context marker on the last entry block (or the head), never on the tail. With caching off, no markers are sent and the layout works the same.

- **Remaining limits:**
  - **Eligible is not a hit:** whether the provider reads an entry also depends on its lifetime, on the minimum cacheable length (the test prefixes are far shorter than 1,024 tokens) and on how it handles the lookback. Only a live run can show that.
  - **A step that appends more than 20 blocks** (many parallel calls) passes the lookback, so the previous marker is not found.
  - **Accepted edits** still invalidate from the first changed entry; an early edit leaves little reusable.
  - **The host's automatic caching** writes an entry at the end of every request, after the changing tail. With a segmented layout that entry can never be read, so it adds write cost for the tail. Whether to keep it is a host configuration choice.
  - **The model sees the task after its working context** in this layout. Its effect on behaviour is not measured.

## 2026-10-22: the opt-in segmented request layout (0.2.0.dev2)

Decision P8 (SPEC 3.1, 8 and 13.17). Offline only: scripted models, and request bodies from Pydantic AI's Anthropic adapter over an in-memory transport with a placeholder key. No provider calls, including token counting.

- **`make check` passes:**
  - ruff check, and ruff format over src, tests, scripts and examples;
  - pyright (`-p pyproject.toml`): 0 errors;
  - pytest: 70 passed, 1 skipped. That is the 60 earlier tests unchanged, plus 10 in `test_request_layout.py`. The skipped module, `test_request_layout_anthropic.py`, needs the `anthropic` package, which this library doesn't depend on.
- **The adapter checks:** the five tests in `test_request_layout_anthropic.py` pass in an environment with the same `pydantic-ai-slim` (2.27.1) and `anthropic` 0.125.0, with this source installed editable.
- **`make transcripts-check` passes:** all 32 stored transcripts are identical, so the default layout is unchanged. None was re-recorded.
- **All 32 scenarios in both layouts** (`test_request_layout.py`), run with the segmented layout and a five-minute cache point:
  - **the same decisions:** in every scenario, the trace record types and statuses, the calls and the endings are the same;
  - **the same text:** every rebuilt request's text is identical apart from the estimate figures, so retained entries, their order, receipts, protection, held-back notes and recovery are unaffected;
  - **where the figures differ:** every request's status line. Held-back notes and PRESSURE lines also differ in `dense_guard`, `guard`, `never_fits`, `over_limit_rearm` and `pressure_and_over_limit`, and ending messages in `pressure_and_over_limit` and the two budget scenarios;
  - **how much:** over 176 calls, the status estimate was 9 to 69 tokens higher (median 22; at most 1.41%, median 0.77%). That is the list content's framing, counted rather than hidden. The scripted provider's own reported usage is derived from the serialized request too, so it rises with it.
- **The request bodies (Anthropic adapter),** with the host's automatic, tool and system caching on:
  - every body carries at most four breakpoints;
  - the context's marker is always on the last entry block (or the task head), never on the changing status block;
  - with caching off, no markers are sent, and the text is the same as the single layout's apart from the estimate figures.
- **Reusable boundaries,** by the documented rules (an exact block prefix, breakpoints, a 20-block lookback), over two turns with an accepted edit:

  | Call | Segmented, with the cache point | Single |
  | --- | --- | --- |
  | turn 1, after the first lookup | the task head (the first call had no entries) | tools and system |
  | turn 1, after the second lookup | the whole previous working context | tools and system |
  | turn 1, after an accepted delete of an early entry | the task head; the entries after it are not reusable | tools and system |
  | turn 2, a new user turn | tools and system (the task head changed) | tools and system |
  | turn 2, after a lookup | the whole previous working context | tools and system |

  **What these checks are:** request structure and cache eligibility, not real cache hits, savings or unchanged model behaviour.

## 2026-10-16: the backstop always on, strict ratio learning removed (0.2.0.dev1)

Decision P7 (SPEC 8 and 13.16). Offline only: scripted models, no provider calls.

- **`make check` passes:**
  - ruff check, and ruff format over src, tests, scripts and examples;
  - pyright (`-p pyproject.toml`): 0 errors;
  - pytest: 60 passed. That is the 57 tests from before P6, plus 3 backstop tests. The 7 strict-learning tests from P6 were removed.
- **The stored transcripts:** all 32 were re-run. Only `over_budget_ignored` changed, and only in estimate figures derived from the learnt ratio. Its calls, guard decisions, held-back record and ending are the same (P7). It was re-recorded, and `make transcripts-check` then passes for all 32.
- **The backstop tests,** with default settings:
  - an edit-like step that grows by 40 characters while the reported input rises by 250 tokens doesn't lower the ratio;
  - an observation of exactly 0.5 lowers it, and one just below doesn't;
  - end to end, with a model reporting real usage, a run whose edit-and-read step has that pattern completes, where without the backstop it ends on the context limit.
- **A check of the tests:** with the threshold patched to 0, the two pattern tests fail: one on its assertion, the other with `ContextLimitError`.
- **The sources** for the capability and the package's entry point are byte-identical to the version before P6, apart from the version string. The output guard is unchanged.

## 2026-10-16: strict ratio learning (0.2.0.dev0)

_Superseded by the entry above (P7): the option this entry describes has been removed._

The `strict_ratio_learning` option (decisions P6, SPEC 8 and 13.16), off by default. Offline only: scripted models, no provider calls.

- **`make check` passes:**
  - ruff check, and ruff format over src, tests, scripts and examples;
  - pyright (`-p pyproject.toml`): 0 errors;
  - pytest: 64 passed. That is the 57 earlier tests unchanged, plus 7 new ones in `test_clm_context.py`.
- **`make transcripts-check` passes:** all 32 stored transcripts are identical. None was re-recorded.
- **The new tests,** with synthetic numbers and a scripted model whose `system` reports real usage:
  - **the calibration pattern:** a step that grew by 40 characters while the reported input rose by 250 tokens. With the option off, the new-text ratio falls to 0.16, and a later request adding 30,000 characters is estimated at 195,750 tokens. With it on, the ratio stays 2.0, and the estimate is 23,250, within the 29,952 limit;
  - **the backstop:** an extending step observed at 0.33 characters per token is ignored;
  - **append-only learning:** on extending steps, the calibration is identical with the option off and on;
  - **end to end:** a run whose second response deletes the first output and reads a similar-sized page, so the request grows by a few characters while the reported input rises by hundreds of tokens. With the option off, the run ends on the context limit ("over the budget itself"), though no reported input passed about 6,000 tokens. With it on, it completes;
  - **a purely appending run:** with the option off and on, the requests, trace records and calibration are identical.
- **The prefix rule matters:** with the guard's test used literally, an empty working context isn't a prefix. Then the first step of every run wouldn't teach the ratio under strict learning, and the purely appending test fails. That was checked by temporarily changing the code, then restoring it.
- **If the option were the default:** an offline comparison that wrote nothing found 1 of the 32 transcripts would differ, `over_budget_ignored`. Its final estimate would be 42,574 tokens instead of 95,668, with the same calls and ending.
- **Build:** `uv build` produces `guarded_clm-0.2.0.dev0` (wheel and sdist). The wheel's metadata says `0.2.0.dev0`, as does `guarded_clm.__version__`.
- **The lockfile:** only the project's own version line changed.

## 2026-10-15: offline verification

**The public suite:** the 32 synthetic scenarios' stored transcripts, and the unit and path tests (see "Public suite and packaging" below). The transcripts are a regression contract. Their only normalisation is the scrubbing of timestamps, run ids, conversation ids, toolset ids and capability ids; prompt wording, token estimates, entry order, revisions, guard decisions, counters, records and errors are compared exactly.

**Randomised offline runs** (not part of the public suite) covered 2,000 seeded conversations. These mixed:
- random tool calls and parallel calls;
- edits, including invalid, protected and coerced ones;
- thoughts, turns and conversations;
- budgets, limits, usage modes and overshoot settings.

**A race:** one seed produced two different outcomes across reruns. In one response, a tool that exceeds its retry limit ends the run, while a concurrently running `edit_context` call may or may not have written its trace record by then. Matching runs therefore don't prove deterministic behaviour for races. The execution configuration is unchanged: sync tools run in worker threads, and edits aren't serialised (SPEC 13.2, decisions O2).

**A comparison with another copy:** before publication, the implementation and the stored transcripts were also compared offline with another copy of the same implementation, kept outside this repository, and they agreed. That comparison shows the two copies behave the same, not that the behaviour is correct; its details are not published.

## Public suite and packaging

- `make check` passes:
  - ruff check, and ruff format over src, tests, scripts and examples;
  - pyright (`-p pyproject.toml`): 0 errors in 14 files;
  - pytest: 57 passed, 33 of them in `test_golden.py`.
- **Build:** `uv build` produces the wheel and sdist. They hold only the package, its licence files and the README; no private metadata or unintended files were found.
- **Clean environments:** see the entry below.

## Clean-environment checks

The built wheel was installed into fresh virtual environments, with only the wheel and pytest, from public PyPI. In each:
- `guarded_clm` imported from site-packages;
- the example ran offline;
- the public test suite passed against the installed package.

| Python | Resolved Pydantic AI stack | Example | Tests |
| --- | --- | --- | --- |
| 3.11.16 | pydantic-ai-slim 2.27.1, pydantic 2.14.1, pydantic-core 2.50.1 | ran, expected output | 57 passed |
| 3.13.15 | the same versions | ran, expected output | 57 passed |

## Packaging note

`guarded_clm.__version__` is `"0.1.0"`, while the distribution metadata (`pyproject.toml`) says `0.1.0.dev0`. This is not a behaviour difference, but the two should agree before a release. Recorded as open question O15 in [decisions.md](decisions.md). **Closed on 2026-10-16 (P6):** both are `0.2.0.dev0`.

