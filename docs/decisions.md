# Decisions

Numbered and dated. A decision changes only through a new entry that supersedes it. Behaviour is defined in [SPEC.md](../SPEC.md); the roadmap is in [implementation-plan.md](implementation-plan.md).

## Taken

**D1 (2026-10-15). The core is provider-neutral and dependency-free.**
`guarded_clm` imports only the standard library: no provider SDK, no agent framework, not even pydantic. Adapters will live outside the core import path. This keeps the core testable offline and importable by any harness. A test enforces it.

**D2 (2026-10-15). Protection is runtime-owned metadata, not inferred from IDs.**
Only the turn lifecycle creates protected entries. Model operations cannot set the flag, since no `protected` key is accepted and `add_note` always creates an unprotected note. Host appends of flagged entries are refused.

This resolves the collision problem: if protection were inferred from an exact pattern such as `^t\d+\.answer$`, a collision-suffixed record such as `t2.answer~2` would silently lose protection. Here the flag travels with the entry. Since IDs are also never renamed (D4), no record ever needs a suffix.

**D3 (2026-10-15). Reserved namespaces, including collision-suffixed forms.**
- **Turn records:** `^t[0-9]+\.(request|answer)(~.*)?$`. Step records: `^s[0-9]+\.(act|obs|rcpt)(~.*)?$`.
- **Models:** may not create either with `add_note`.
- **Hosts:** may not create turn records through `append_runtime_entries`.
- **Why the `~` forms and leading zeros are included:** a model must not create a fake turn record, or occupy an ID the runtime will need later, such as `t5.request` or `s3.obs`.

Models may still replace or delete **unprotected** runtime entries.

**D4 (2026-10-15). Duplicates fail; nothing is renamed.**
- **Model edits:** a model-supplied duplicate ID rejects the transaction.
- **Host appends and lifecycle materialisation:** a duplicate ID fails the whole call with no change, so lifecycle duplication is rejected explicitly instead of creating a second canonical record.
- **Why there is no suffix policy:** reserved namespaces (D3) mean runtime IDs cannot be taken by a model, so a deterministic suffix policy is unnecessary.

**D5 (2026-10-15). The canonical serialisation for limits.**
- **Function:** `json.dumps` of `{"format": "clm-context/v1", "entries": [...]}`.
- **Key order:** `format`, `entries`. Per entry: `id`, `role`, `body`, then `protected` only when true.
- **Options:** `ensure_ascii=False`, separators `(",", ":")`, no trailing newline.
- **Measurement:** the 256,000-byte limit applies to its UTF-8 encoding.
- **Why include `protected`:** the byte check covers the model-visible form, which includes it.
- **Why `ensure_ascii=False`:** the byte count then reflects real UTF-8 size rather than `\uXXXX` escapes.

**D6 (2026-10-15). IDs are validated with `fullmatch`.**
With `re.match`, a pattern ending in `$` also accepts a trailing newline (`"a\n"`). The specified pattern is therefore applied as a full match. clm-lib's existing context ID check has the same trailing-newline behaviour. That is recorded there as a finding and is not changed here.

**D7 (2026-10-15). Turn records materialise at the next request preparation, idempotently.**
- **When:** `start_turn` for turn N+1 queues turn N's records. The first `prepare_request()` of turn N+1 adds them in one revision: `tN.request` for N ≥ 2, then `tN.answer`. Later preparations add nothing.
- **Why:** this follows the supplied design (materialisation at the next request build). It also gives the future request builder one idempotent hook to call before every request.
- **Failure:** a structural failure leaves the store unchanged and the records pending.

**D8 (2026-10-15). Role mapping for turn records.**
- **Requests** use `observation`: input from outside the agent.
- **Answers** use `assistant`.

Neither is `note`, which is reserved by convention for model-written notes (`add_note`).

**D9 (2026-10-15). The task anchor and current request live outside the working context.**
- **Task anchor:** turn 1's request becomes the task. It is never an entry, so it is not subject to the body limit (budget enforcement is planned).
- **Current request:** while its turn is active it is host-owned and unreachable by edits. It must already be a valid body, because it becomes `tN.request` later.

**D10 (2026-10-15). An ID deleted in a transaction cannot be re-added in the same transaction.**
This avoids ambiguous receipts, such as a "rewritten" entry whose role and position changed. A later transaction may reuse the ID.

**D11 (2026-10-15). Pressure must precede the working limit.**
A configuration whose pressure point (`pressure_ratio × budget`) is above the working limit (`budget − reserve`) is rejected, because pressure could never be signalled before the limit.

**D12 (2026-10-15). Runtime entries and edits are allowed only in an active, prepared turn.**
Calls before the turn's first `prepare_request()`, or after the final answer, raise `LifecycleError`. The model has seen no request yet, or its turn is over.

**D13 (2026-10-15). The trust boundary.**
Host APIs are trusted application interfaces that validate model-influenced input. They are not a sandbox; model-generated code must never run in-process.

**D14 (2026-10-15). No load/restore yet.**
There is no persistence API. If one is added, it will be separate from model-edit operations, will validate the complete state, and will preserve the runtime-owned protection metadata.

## Unresolved (to be settled before the chunk that needs them)

- **U1. Escaping in the request builder.** The supplied rendering instruction to escape every less-than sign is malformed: it maps the character to itself. The intended encoding needs clarification before chunk 2.
- **U2. The 144-call per-turn cap.** What it counts (main-model calls or provider attempts; whether retries count) is undecided.
- **U3. Recovery re-arming.** How the one-request reserve allowance behaves after the context returns below the limit, to be resolved against existing clm-lib behaviour before implementation.
- **U4. Output-guard edge cases.** Placeholder overflow (when the placeholder for held output itself does not fit), and a single output too large on its own.
- **U5. Later calls after a held output.** Whether calls after a held non-edit output still execute.
- **U6. Provider content blocks.** Provider-required content blocks (for example thinking blocks) and their compatibility with reconstructed requests.
- **U7. Usage mapping.** How total provider input usage, including cache reads and writes, maps to valid append-only estimator observations.
- **U8. Protected-history exhaustion.** What happens when protected records alone exhaust the structural limits or the budget. Structurally, it currently fails explicitly with no change (D7).
- **U9. Tools with side effects.** Adapter support is future work; the initial task tools are read-only.
- **U10. An unanswered turn.** Starting a new turn currently requires a recorded answer. How a turn that ends without an answer (for example by recovery termination) is closed depends on the recovery state machine (chunk 2).
- **U11. Package name.** The availability of `guarded-clm` on public package indexes has not been verified.
