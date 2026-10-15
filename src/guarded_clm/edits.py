"""Edit operations and their results (SPEC §4)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from .entries import body_problem, id_problem


@dataclass(frozen=True, slots=True)
class Delete:
    ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Replace:
    id: str
    body: str


@dataclass(frozen=True, slots=True)
class AddNote:
    id: str
    body: str


Operation = Delete | Replace | AddNote
Outcome = Literal["accepted", "unchanged", "rejected"]

_KEYS: dict[str, frozenset[str]] = {
    "delete": frozenset({"op", "ids"}),
    "replace": frozenset({"op", "id", "body"}),
    "add_note": frozenset({"op", "id", "body"}),
}


class OperationError(ValueError):
    """An operation is malformed. Raised by ``parse_operation``; the store turns it into a
    rejected transaction."""


def parse_operation(raw: Any) -> Operation:
    """Parse one operation from its JSON form (or accept an already typed operation).

    Forms: ``{"op": "delete", "ids": [...]}``, ``{"op": "replace", "id": ..., "body": ...}``,
    ``{"op": "add_note", "id": ..., "body": ...}``. Keys must match exactly; values must have
    the right types. Existence, protection and reserved-namespace rules are checked by the
    store against the candidate state, not here.
    """
    if isinstance(raw, Delete | Replace | AddNote):
        raw = _as_mapping(raw)
    if not isinstance(raw, Mapping):
        raise OperationError(f"an operation must be an object, got {type(raw).__name__}")
    kind = raw.get("op")
    if not isinstance(kind, str) or kind not in _KEYS:
        raise OperationError(f"unknown op {kind!r} (allowed: delete, replace, add_note)")
    keys = frozenset(raw)
    if keys != _KEYS[kind]:
        missing = sorted(_KEYS[kind] - keys)
        extra = sorted(keys - _KEYS[kind])
        raise OperationError(f"{kind}: missing keys {missing}, unexpected keys {extra}")
    if kind == "delete":
        ids = raw["ids"]
        if not isinstance(ids, list | tuple) or not ids:
            raise OperationError("delete: ids must be a non-empty list")
        for i in ids:
            problem = id_problem(i)
            if problem:
                raise OperationError(f"delete: {problem}")
        if len(set(ids)) != len(ids):
            raise OperationError("delete: ids contain duplicates")
        return Delete(tuple(ids))
    entry_id, body = raw["id"], raw["body"]
    problem = id_problem(entry_id) or body_problem(body)
    if problem:
        raise OperationError(f"{kind}: {problem}")
    return Replace(entry_id, body) if kind == "replace" else AddNote(entry_id, body)


def _as_mapping(op: Operation) -> dict[str, Any]:
    if isinstance(op, Delete):
        return {"op": "delete", "ids": list(op.ids)}
    if isinstance(op, Replace):
        return {"op": "replace", "id": op.id, "body": op.body}
    return {"op": "add_note", "id": op.id, "body": op.body}


@dataclass(frozen=True, slots=True)
class EditResult:
    """The outcome of one edit transaction.

    - ``accepted``: the context changed and its revision advanced by exactly one.
    - ``unchanged``: every operation was valid but the final state equals the starting state;
      no new revision.
    - ``rejected``: nothing was applied; ``reason`` says why and ``operation_index`` (0-based)
      names the offending operation when there is one.

    ID lists are relative to the state at the start of the transaction: ``removed`` IDs no
    longer exist, ``added`` IDs did not exist, ``rewritten`` IDs exist in both with a new body.
    Removed bodies are never included.
    """

    outcome: Outcome
    base_revision: int
    revision: int
    removed: tuple[str, ...] = ()
    added: tuple[str, ...] = ()
    rewritten: tuple[str, ...] = ()
    reason: str | None = None
    operation_index: int | None = None

    @property
    def accepted(self) -> bool:
        return self.outcome == "accepted"

    def receipt(self) -> dict[str, Any]:
        """A compact, model-visible receipt (IDs and reasons only)."""
        out: dict[str, Any] = {"outcome": self.outcome, "revision": self.revision}
        if self.outcome == "rejected":
            out["reason"] = self.reason
            if self.operation_index is not None:
                out["operation"] = self.operation_index
        else:
            out.update(
                removed=list(self.removed), added=list(self.added), rewritten=list(self.rewritten)
            )
        return out
