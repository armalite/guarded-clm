# Results

**Offline verification only.** There are no model runs, no provider calls and no performance findings. Nothing here measures task quality, cost or speed with a real model.

## 2026-10-15: faithful port and differential verification

**What was compared:**
- **guarded-clm:** this repository, in its own environment.
- **The reference:** an untouched copy of the source implementation, in a separate environment, with the same pinned dependency versions, from public PyPI:
  - Python 3.11.16;
  - `pydantic-ai-slim` 2.27.1, `pydantic` 2.14.1, `pydantic-core` 2.50.1.

Each ran in its own process.

**Source comparison:** all seven modules have identical abstract syntax trees once docstrings and the package name in imports are normalised. The model-visible `edit_context` docstring is compared verbatim, not normalised. The textual changes are limited to provenance docstrings and comments.

**Behavioural comparison:**

| Check | Outcome |
| --- | --- |
| The source implementation's own test suite, run against guarded-clm (imports renamed only; its recorded transcripts stay outside this repository) | 40 passed, including all 22 of its recorded scenario transcripts, identical |
| The same suite against the reference itself | 40 passed |
| This repository's 32 public scenarios, recorded from the reference and from guarded-clm, 3 times each | all byte-identical, and stable across runs |
| 2,000 seeded random conversations per implementation (random tool calls, parallel calls, edits including invalid, protected and coerced ones, thoughts, turns, conversations, budgets, limits, usage modes, overshoot settings) | 1,999 identical. 1 (seed 1558) is non-deterministic **in both implementations**: see below |

**Seed 1558** is a concurrency race the source permits.
- **What happens:** in one response, a tool that exceeds its retry limit ends the run, while a concurrently running `edit_context` call may or may not have written its trace record by then.
- **Reruns:** five reruns in each implementation produced the same two outcomes in both.

So matching runs do not prove deterministic parity for races. The source's execution configuration is preserved unchanged: sync tools run in worker threads, and edits are not serialised (SPEC 13.2, decisions O2).

**What the random conversations covered:**
- **Endings:** 2,320 completed runs, 345 step-limit endings, 325 retry-limit endings (Pydantic AI's `UnexpectedModelBehavior`), 297 `ContextLimitError`, 154 call-cap endings and 61 `BudgetExceededError`.
- **Records:** 3,642 edits, 4,199 held-back outputs, 819 refetches, 195 over-limit requests, 358 limit endings and 14 protected refusals.

**Permitted normalisation:**
- **Inside transcripts,** by the scenario harness itself: timestamps, run ids, conversation ids, toolset ids and capability ids are scrubbed. Prompt wording, token estimates, entry order, revisions, guard decisions, counters, records and errors are compared exactly.
- **Between the implementations:** only the import package name (`clm_context` against `guarded_clm`) differs.

**What was not run:** the source's transcript generator, which needs its private origin checkout.

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
