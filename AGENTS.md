# Instructions for coding agents

## Purpose and boundary

guarded-clm publishes `ClmContext`, a Pydantic AI capability. The model manages its own working context within a token budget; the runtime validates edits, protects earlier turns and enforces the limit.

## The prime directive: no behaviour drift

- **The stored transcripts are the contract** (`tests/golden/data/`, checked by `tests/test_golden.py`). Never edit them by hand, never loosen the comparison, and never re-record them to make a change pass.
- **A transcript failure means the library is wrong.** Fix the library. If you believe a transcript is wrong, stop and say so.
- **Internal refactors** (names, structure, typing, comments) are fine only if every transcript stays identical.
- **A behaviour change** is anything the model could see, or any change to results, trace records or endings. It needs an approved decision in docs/decisions.md that says how the transcripts change. Ship it behind an option that is off by default.
- **Compatibility strings stay as they are:** the context format, `edit_context`, the trace variant and record types, and the internal ids (decisions P2).

## Sources of truth

- [SPEC.md](SPEC.md): the exact behaviour (normative).
- [docs/implementation-plan.md](docs/implementation-plan.md): the roadmap.
- [docs/decisions.md](docs/decisions.md): decisions and open questions.
- [docs/architecture.md](docs/architecture.md): the structure.
- [docs/results.md](docs/results.md): verification.
- `docs/HANDOFF.md`: the local, gitignored resume point. Read it first and update it at milestones.

Link between these documents; don't duplicate their contents.

## Rules

- **Offline only.** Tests use scripted models (`FunctionModel`). No provider API calls, including token counting, and no spending.
- **Never** commit, push, tag, release or publish without explicit permission, and never create remotes.
- **Never** print or commit credentials, private registry URLs, employer or internal-system names, private data or private results.
- **Dependencies:** public PyPI only. The runtime dependency is `pydantic-ai-slim`, pinned to the tested minor (P4).
- **Licence:** no code, prompt or notice text from the CC BY-NC reference repository of the CLM paper. Keep the clm-lib attribution in NOTICE and in the derived files.

## Commands

```bash
make install          # uv sync
make check            # ruff, ruff format --check, pyright -p pyproject.toml, pytest (offline)
make transcripts-check
uv build
```

## Final report format

- Status;
- what was implemented;
- changed files;
- checks and their results;
- every execution or API call, and any spend;
- deviations;
- open questions;
- docs and handoff updates;
- the next step;
- suggested commit messages, without attribution trailers.
