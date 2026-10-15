"""Working-context entries, ID namespaces, structural limits and serialisation (SPEC §3)."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Final

FORMAT: Final = "clm-context/v1"
ROLES: Final = ("assistant", "observation", "note", "summary", "receipt")
ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:~-]{0,63}$")

MAX_ENTRIES: Final = 200
MAX_BODY_CODEPOINTS: Final = 24_000
MAX_TOTAL_CODEPOINTS: Final = 120_000
MAX_SERIALIZED_BYTES: Final = 256_000

# Reserved namespaces (SPEC §5). Turn records are created only by the turn lifecycle; step
# records are created only by the host runtime. Both include any "~" suffixed form, and digits
# with leading zeros, so no look-alike can be created by a model edit.
TURN_RECORD_PATTERN: Final = re.compile(r"^t[0-9]+\.(?:request|answer)(?:~.*)?$")
STEP_RECORD_PATTERN: Final = re.compile(r"^s[0-9]+\.(?:act|obs|rcpt)(?:~.*)?$")


class ValidationError(ValueError):
    """A value violates the entry format or a structural limit."""


def id_problem(value: Any) -> str | None:
    if not isinstance(value, str):
        return f"id must be a string, got {type(value).__name__}"
    # fullmatch: with re.match, "$" would also accept a trailing newline.
    if not ID_PATTERN.fullmatch(value):
        return f"invalid id {value!r} (must match {ID_PATTERN.pattern})"
    return None


def body_problem(value: Any) -> str | None:
    if not isinstance(value, str):
        return f"body must be a string, got {type(value).__name__}"
    if len(value) > MAX_BODY_CODEPOINTS:
        return f"body has {len(value)} code points (limit {MAX_BODY_CODEPOINTS})"
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return "body is not valid Unicode text (it contains a lone surrogate)"
    return None


def is_turn_record_id(entry_id: str) -> bool:
    return TURN_RECORD_PATTERN.fullmatch(entry_id) is not None


def is_step_record_id(entry_id: str) -> bool:
    return STEP_RECORD_PATTERN.fullmatch(entry_id) is not None


def is_reserved_id(entry_id: str) -> bool:
    """IDs a model edit may never create (turn and step namespaces)."""
    return is_turn_record_id(entry_id) or is_step_record_id(entry_id)


@dataclass(frozen=True, slots=True)
class Entry:
    """One working-context entry. Immutable.

    ``protected`` is runtime-owned metadata: only the store sets it, for turn records created
    by the lifecycle. Entries supplied by callers must leave it ``False``.
    """

    id: str
    role: str
    body: str
    protected: bool = False

    def __post_init__(self) -> None:
        problem = (
            id_problem(self.id)
            or (None if self.role in ROLES else f"invalid role {self.role!r} (allowed: {ROLES})")
            or body_problem(self.body)
            or (None if isinstance(self.protected, bool) else "protected must be a bool")
        )
        if problem:
            raise ValidationError(problem)

    def to_dict(self) -> dict[str, Any]:
        """The model-visible form: ``protected`` appears only when true."""
        out: dict[str, Any] = {"id": self.id, "role": self.role, "body": self.body}
        if self.protected:
            out["protected"] = True
        return out


def serialize(entries: Iterable[Entry]) -> str:
    """The canonical serialisation used for the byte limit and as the model-visible document.

    ``json.dumps`` of ``{"format": "clm-context/v1", "entries": [...]}`` with keys in this
    order (``format``, ``entries``; per entry ``id``, ``role``, ``body``, then ``protected``
    only when true), ``ensure_ascii=False``, separators ``(",", ":")`` and no trailing newline.
    The byte limit applies to its UTF-8 encoding.
    """
    doc = {"format": FORMAT, "entries": [e.to_dict() for e in entries]}
    return json.dumps(doc, ensure_ascii=False, separators=(",", ":"))


def serialized_bytes(entries: Iterable[Entry]) -> int:
    return len(serialize(entries).encode("utf-8"))


def structure_problem(entries: tuple[Entry, ...] | list[Entry]) -> str | None:
    """The first structural limit a complete entry list violates, or None."""
    if len(entries) > MAX_ENTRIES:
        return f"{len(entries)} entries (limit {MAX_ENTRIES})"
    seen: set[str] = set()
    for e in entries:
        if e.id in seen:
            return f"duplicate id {e.id!r}"
        seen.add(e.id)
    total = sum(len(e.body) for e in entries)
    if total > MAX_TOTAL_CODEPOINTS:
        return f"bodies total {total} code points (limit {MAX_TOTAL_CODEPOINTS})"
    size = serialized_bytes(entries)
    if size > MAX_SERIALIZED_BYTES:
        return f"serialised context is {size} bytes (limit {MAX_SERIALIZED_BYTES})"
    return None
