# Results

**Offline verification only.** There are no model runs, no provider calls and no performance findings. Nothing here measures task quality, cost or speed with a real model.

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

`guarded_clm.__version__` is `"0.1.0"`, while the distribution metadata (`pyproject.toml`) says `0.1.0.dev0`. This is not a behaviour difference, but the two should agree before a release. Recorded as open question O15 in [decisions.md](decisions.md).

