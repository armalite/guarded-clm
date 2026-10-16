# Results

**Offline verification only.** There are no model runs, no provider calls and no performance findings. Nothing here measures task quality, cost or speed with a real model.

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

