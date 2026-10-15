"""The working context: entries, their limits and validation, and the host-side revision store.

Derived from clm-lib (`src/clm_lib/context.py`, github.com/armalite/clm-lib, commit
024fc76a8e79468e3d160112844c7aa9bd2e94ac). Only the parts this design uses are kept: the schema, the limits,
validation and the revision store. The file mirror and read-back (for code edits in a sandbox) are left out, because
edits here are structured operations.

    clm-lib is licensed under the Apache License, Version 2.0 (the "License"); you may not use this file except in
    compliance with the License. You may obtain a copy of the License at
    http://www.apache.org/licenses/LICENSE-2.0. Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND,
    either express or implied. See the License for the specific language governing permissions and limitations.

An edit is validated by serializing the edited entries as the clm-lib file format,
`{"format": "clm-context/v1", "entries": [{"id", "role", "body"}, ...]}`, and parsing that back with the file
format's checks, exactly as the tested design does. Revision numbers and hashes live in `ContextStore`.
"""

import dataclasses
import hashlib
import json
import re

FORMAT = "clm-context/v1"
ALLOWED_ROLES = frozenset({"assistant", "observation", "note", "summary", "receipt"})
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:~-]{0,63}$")


@dataclasses.dataclass(frozen=True)
class Entry:
    id: str
    role: str
    body: str

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "role": self.role, "body": self.body}


@dataclasses.dataclass(frozen=True)
class ContextLimits:
    max_file_bytes: int = 256_000
    max_entries: int = 200
    max_body_chars: int = 24_000
    max_total_chars: int = 120_000


@dataclasses.dataclass(frozen=True)
class Revision:
    number: int
    entries: tuple[Entry, ...]
    sha256: str
    parent: int | None
    source: str  # "init" | "runtime" | "model_edit"
    step: int


class ContextValidationError(ValueError):
    """Edited entries violate the documented format or limits."""


def serialize(entries: tuple[Entry, ...] | list[Entry]) -> str:
    doc = {"format": FORMAT, "entries": [e.to_dict() for e in entries]}
    return json.dumps(doc, ensure_ascii=False, indent=1) + "\n"


def content_hash(entries: tuple[Entry, ...] | list[Entry]) -> str:
    canonical = json.dumps([e.to_dict() for e in entries], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def total_chars(entries: tuple[Entry, ...] | list[Entry]) -> int:
    return sum(len(e.body) for e in entries)


def render_entries(entries: tuple[Entry, ...] | list[Entry]) -> str:
    """JSON lines with '<' escaped, so entry text cannot forge section tags."""
    lines = [
        json.dumps({"id": e.id, "role": e.role, "body": e.body}, ensure_ascii=False).replace("<", "\\u003c")
        for e in entries
    ]
    return "\n".join(lines)


def parse_candidate(raw: bytes, limits: ContextLimits) -> tuple[Entry, ...]:
    """Validate a serialized context and return its entries, or raise ContextValidationError."""
    if len(raw) > limits.max_file_bytes:
        raise ContextValidationError(f"file is {len(raw)} bytes; limit {limits.max_file_bytes}")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContextValidationError("file is not valid UTF-8") from exc
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ContextValidationError(f"invalid JSON: {exc.msg} at line {exc.lineno}") from exc
    if not isinstance(doc, dict):
        raise ContextValidationError("top level must be an object")
    if set(doc) != {"format", "entries"}:
        extra = sorted(set(doc) - {"format", "entries"})
        missing = sorted({"format", "entries"} - set(doc))
        raise ContextValidationError(f"top-level keys must be format, entries (extra={extra}, missing={missing})")
    if doc["format"] != FORMAT:
        raise ContextValidationError(f"format must be {FORMAT!r}")
    items = doc["entries"]
    if not isinstance(items, list):
        raise ContextValidationError("entries must be a list")
    if len(items) > limits.max_entries:
        raise ContextValidationError(f"{len(items)} entries; limit {limits.max_entries}")
    seen: set[str] = set()
    out: list[Entry] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict) or set(item) != {"id", "role", "body"}:
            raise ContextValidationError(f"entry {i} must have exactly id, role, body")
        eid, role, body = item["id"], item["role"], item["body"]
        if not all(isinstance(v, str) for v in (eid, role, body)):
            raise ContextValidationError(f"entry {i}: id, role, body must be strings")
        if not ID_RE.match(eid):
            raise ContextValidationError(f"entry {i}: invalid id {eid[:40]!r}")
        if eid in seen:
            raise ContextValidationError(f"duplicate id {eid!r}")
        seen.add(eid)
        if role not in ALLOWED_ROLES:
            raise ContextValidationError(
                f"entry {eid!r}: role {role[:20]!r} not allowed (allowed: {sorted(ALLOWED_ROLES)})"
            )
        if len(body) > limits.max_body_chars:
            raise ContextValidationError(f"entry {eid!r}: body {len(body)} chars; limit {limits.max_body_chars}")
        out.append(Entry(eid, role, body))
    total = total_chars(out)
    if total > limits.max_total_chars:
        raise ContextValidationError(f"total body chars {total}; limit {limits.max_total_chars}")
    return tuple(out)


@dataclasses.dataclass
class EditOutcome:
    """The result of one edit."""

    status: str  # "unchanged_write" | "accepted" | "rejected"
    reason: str = ""
    before: Revision | None = None
    after: Revision | None = None


@dataclasses.dataclass
class ContextStore:
    """The authoritative, host-side record of accepted context revisions."""

    limits: ContextLimits = dataclasses.field(default_factory=ContextLimits)
    revisions: list[Revision] = dataclasses.field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.revisions:
            self.revisions.append(Revision(0, (), content_hash(()), None, "init", 0))

    @property
    def current(self) -> Revision:
        return self.revisions[-1]

    @property
    def entries(self) -> tuple[Entry, ...]:
        return self.current.entries

    def commit(self, entries: tuple[Entry, ...] | list[Entry], source: str, step: int) -> Revision:
        entries = tuple(entries)
        rev = Revision(self.current.number + 1, entries, content_hash(entries), self.current.number, source, step)
        self.revisions.append(rev)
        return rev

    def append(self, new: list[Entry], source: str, step: int) -> Revision:
        """Append runtime entries; an id that collides with an existing one gets a `~N` suffix."""
        existing = {e.id for e in self.entries}
        fixed: list[Entry] = []
        for e in new:
            eid, n = e.id, 2
            while eid in existing:
                eid, n = f"{e.id}~{n}", n + 1
            existing.add(eid)
            fixed.append(Entry(eid, e.role, e.body))
        return self.commit(self.entries + tuple(fixed), source, step)
