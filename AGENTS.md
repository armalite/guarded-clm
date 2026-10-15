# Instructions for coding agents

## Purpose and boundary

guarded-clm is a small, provider-neutral Python library. The model manages its editable working context; the runtime validates edits, protects designated conversation records and enforces the configured budget. The core (`src/guarded_clm/`) has no runtime dependencies and must not import a provider SDK or an agent framework. Adapters (planned) live outside the core import path.

## Sources of truth

- [SPEC.md](SPEC.md): behaviour (implemented and planned, separately marked).
- [docs/implementation-plan.md](docs/implementation-plan.md): the chunk roadmap and status.
- [docs/decisions.md](docs/decisions.md): numbered decisions and unresolved items.
- [docs/architecture.md](docs/architecture.md): structure and boundaries.
- `docs/HANDOFF.md`: local, gitignored resume point. Read it first when resuming; update it at milestones.

Link between these documents; do not duplicate their contents. Keep the plan, spec, architecture and handoff consistent when behaviour changes.

## Status and limits

- Offline only. No provider API calls (including token counting), no spending.
- Never commit, push, tag, release or publish without explicit permission. Never create remotes.
- Never print or commit credentials. Do not add employer names, private data or private experiment results.
- Write original code. Do not copy code, prompts or notice text from the reference CLM repository or other projects.
- Work one planned chunk at a time; later chunks are not authorised automatically.

## Commands

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest
uv build
```

## Final report format

Status; what was implemented (public API); changed files; checks and results; every execution or API call and spend; deviations; open questions; docs and handoff updates; next step and resume point; suggested commit messages without attribution trailers.
