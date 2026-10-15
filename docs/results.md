# Results

**Offline verification only.** There are no model runs and no performance findings. Nothing in this repository has called a model provider.

## 2026-10-15: chunk 1 (core)

Commands, run locally with uv and Python 3.12:

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy          # strict, over src, tests and examples
uv run pytest
uv build
```

**Results:**
- ruff and format are clean;
- mypy (strict) found no issues in 11 source files;
- **82 passed**;
- `uv build` produced the sdist and the wheel.

**Clean-environment check:** a new Python 3.11 virtual environment holding only the built wheel imported `guarded_clm` from site-packages, and ran `examples/basic_turns.py` in isolated mode, with the expected output.

**What the tests cover:**
- configuration boundaries;
- ID, role and body validation, including multibyte text and lone surrogates;
- the entry-count, total-code-point and UTF-8 byte limits;
- the exact canonical serialisation;
- snapshot immutability;
- ordered operations and receipts;
- full rollback after a later invalid operation;
- stale and invalid revisions;
- unchanged transactions;
- same-transaction interactions;
- protected delete and replace, including mixed transactions;
- forged protection and reserved IDs;
- the task anchor and the first request;
- materialisation exactly once, and idempotent preparation;
- protection turned off;
- the current request staying host-owned;
- intermediate assistant output staying unprotected;
- atomic failure of a structurally impossible turn transition;
- the example;
- the core importing no provider or framework.

**A defect found and fixed during the chunk:** ID validation now uses `fullmatch`, because `re.match` with `$` accepted a trailing newline ([decisions D6](decisions.md)).
