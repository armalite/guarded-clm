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

## Superseded (the preliminary core, 2026-10-15)

Kept for the record; superseded by P1.

- **Preliminary decisions D1 to D14** covered a stdlib-only core, protection as runtime metadata, reserved turn and step namespaces, no renaming of duplicates, compact serialisation for limits, `fullmatch` for ids, materialisation at preparation, role mapping, the task anchor, no same-transaction re-add, pressure before the limit, lifecycle states, the trust boundary, and no load/restore. **All are superseded.** The current behaviour differs on most of them, and SPEC.md describes it.
- **Preliminary unresolved items U1 to U11** are settled by the current behaviour where it defines them, or carried as O items above:
  - **escaping:** every `<` is written as the JSON escape `\u003c` (SPEC 3);
  - **the call cap:** per run (SPEC 10);
  - **recovery:** re-arms within a turn and resets on a new turn (SPEC 9);
  - **the guard's held-output rules:** SPEC 7;
  - **the remaining items:** O1, O4 and O6.
