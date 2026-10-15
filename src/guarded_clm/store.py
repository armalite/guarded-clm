"""The authoritative working-context store (SPEC §3 to §5).

Trust boundary: the store's methods are trusted host APIs. They validate everything they are
given, so a model's edit (passed through ``apply_edits``) cannot break the format, the limits,
protection or the reserved namespaces. They are not a sandbox: code running in the host
process can always reach Python internals. Model-generated code must never run in-process.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from .edits import AddNote, Delete, EditResult, OperationError, Replace, parse_operation
from .entries import (
    Entry,
    body_problem,
    id_problem,
    is_reserved_id,
    is_turn_record_id,
    serialize,
    serialized_bytes,
    structure_problem,
)


@dataclass(frozen=True, slots=True)
class Snapshot:
    """An immutable view of one revision. Safe to keep and share: later changes never alter
    it, and nothing in it can change the live store."""

    revision: int
    entries: tuple[Entry, ...]

    def get(self, entry_id: str) -> Entry | None:
        return next((e for e in self.entries if e.id == entry_id), None)

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(e.id for e in self.entries)

    def to_dict(self) -> dict[str, Any]:
        """A fresh, mutable copy of the model-visible document."""
        return {"format": "clm-context/v1", "entries": [e.to_dict() for e in self.entries]}

    def to_json(self) -> str:
        """The canonical serialisation (``entries.serialize``)."""
        return serialize(self.entries)

    @property
    def serialized_bytes(self) -> int:
        return serialized_bytes(self.entries)

    @property
    def total_codepoints(self) -> int:
        return sum(len(e.body) for e in self.entries)


@dataclass(frozen=True, slots=True)
class ChangeResult:
    """The outcome of a host-side change (runtime entries or turn records)."""

    ok: bool
    revision: int
    added: tuple[str, ...] = ()
    reason: str | None = None


class ContextStore:
    """Ordered, uniquely identified entries with a revision number.

    Revision 0 is the empty initial state. Each accepted change, of any source, advances the
    revision by exactly one; rejected and unchanged transactions do not.
    """

    __slots__ = ("_entries", "_revision")

    def __init__(self) -> None:
        self._entries: tuple[Entry, ...] = ()
        self._revision = 0

    @property
    def revision(self) -> int:
        return self._revision

    def __len__(self) -> int:
        return len(self._entries)

    def snapshot(self) -> Snapshot:
        return Snapshot(self._revision, self._entries)

    # ------------------------------------------------------------ model edits
    def apply_edits(self, operations: Any, *, expected_revision: Any) -> EditResult:
        """Apply a model's edit transaction: all operations or none.

        ``operations`` and ``expected_revision`` are treated as untrusted model input: any
        problem yields a ``rejected`` result rather than an exception.
        """
        base = self._revision

        def reject(reason: str, index: int | None = None) -> EditResult:
            return EditResult("rejected", base, base, reason=reason, operation_index=index)

        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
            return reject("expected_revision must be an integer")
        if expected_revision != base:
            return reject(f"stale revision {expected_revision}; the current revision is {base}")
        if not isinstance(operations, list | tuple) or not operations:
            return reject("operations must be a non-empty list")
        ops = []
        for i, raw in enumerate(operations):
            try:
                ops.append(parse_operation(raw))
            except OperationError as exc:
                return reject(str(exc), i)
        original = {e.id: e for e in self._entries}
        candidate: list[Entry] = list(self._entries)
        for i, op in enumerate(ops):
            position = {e.id: n for n, e in enumerate(candidate)}
            if isinstance(op, Delete):
                for entry_id in op.ids:
                    if entry_id not in position:
                        return reject(f"delete: unknown id {entry_id!r}", i)
                    if candidate[position[entry_id]].protected:
                        return reject(f"delete: {entry_id!r} is protected", i)
                drop = set(op.ids)
                candidate = [e for e in candidate if e.id not in drop]
            elif isinstance(op, Replace):
                if op.id not in position:
                    return reject(f"replace: unknown id {op.id!r}", i)
                current = candidate[position[op.id]]
                if current.protected:
                    return reject(f"replace: {op.id!r} is protected", i)
                candidate[position[op.id]] = Entry(op.id, current.role, op.body)
            else:
                assert isinstance(op, AddNote)
                if is_reserved_id(op.id):
                    return reject(f"add_note: {op.id!r} is in a reserved runtime namespace", i)
                if op.id in position:
                    return reject(f"add_note: id {op.id!r} already exists", i)
                if op.id in original:
                    return reject(
                        f"add_note: id {op.id!r} was deleted in this transaction and cannot be "
                        "reused in the same transaction",
                        i,
                    )
                candidate.append(Entry(op.id, "note", op.body))
        problem = structure_problem(candidate)
        if problem:
            return reject(f"the result would exceed a structural limit: {problem}")
        final = tuple(candidate)
        if final == self._entries:
            return EditResult("unchanged", base, base)
        final_ids = {e.id for e in final}
        result = EditResult(
            "accepted",
            base,
            base + 1,
            removed=tuple(e.id for e in self._entries if e.id not in final_ids),
            added=tuple(e.id for e in final if e.id not in original),
            rewritten=tuple(
                e.id for e in final if e.id in original and original[e.id].body != e.body
            ),
        )
        self._commit(final)
        return result

    # ------------------------------------------------------------ host changes
    def append(self, entries: Sequence[Entry]) -> ChangeResult:
        """Append host-created runtime entries (for example step records) in one revision.

        Entries must be unprotected and outside the turn-record namespace, which only the turn
        lifecycle may use. A duplicate ID or a structural violation fails the whole call
        (nothing is appended); no ID is ever renamed.
        """
        for e in entries:
            if not isinstance(e, Entry):
                return self._fail(f"expected Entry objects, got {type(e).__name__}")
            if e.protected:
                return self._fail(f"{e.id!r}: protection is set only by the turn lifecycle")
            if is_turn_record_id(e.id):
                return self._fail(f"{e.id!r}: the turn-record namespace is lifecycle-owned")
        return self._append_checked(tuple(entries))

    def _append_records(self, entries: Iterable[Entry]) -> ChangeResult:
        """Lifecycle-only: append turn records (possibly protected) in one revision."""
        return self._append_checked(tuple(entries))

    def _append_checked(self, entries: tuple[Entry, ...]) -> ChangeResult:
        if not entries:
            return self._fail("nothing to append")
        existing = {e.id for e in self._entries}
        for e in entries:
            problem = id_problem(e.id) or body_problem(e.body)
            if problem:
                return self._fail(problem)
            if e.id in existing:
                return self._fail(f"id {e.id!r} already exists")
        candidate = self._entries + entries
        problem = structure_problem(candidate)
        if problem:
            return self._fail(f"the result would exceed a structural limit: {problem}")
        self._commit(candidate)
        return ChangeResult(True, self._revision, tuple(e.id for e in entries))

    def _fail(self, reason: str) -> ChangeResult:
        return ChangeResult(False, self._revision, reason=reason)

    def _commit(self, entries: tuple[Entry, ...]) -> None:
        self._entries = entries
        self._revision += 1
