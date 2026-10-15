"""Entries, structural limits, serialisation, snapshots and edit transactions."""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import pytest

from guarded_clm import (
    MAX_BODY_CODEPOINTS,
    MAX_ENTRIES,
    MAX_SERIALIZED_BYTES,
    MAX_TOTAL_CODEPOINTS,
    AddNote,
    ContextStore,
    Delete,
    Entry,
    Replace,
    ValidationError,
    is_reserved_id,
    serialize,
)


def store_with(*entries: Entry) -> ContextStore:
    s = ContextStore()
    if entries:
        assert s.append(entries).ok
    return s


def add(i: str, body: str = "b") -> dict[str, Any]:
    return {"op": "add_note", "id": i, "body": body}


# ------------------------------------------------------------ entries and ids
@pytest.mark.parametrize("good", ["a", "A9", "x" * 64, "s1.act~2", "n:1_a.b-c~d", "9lives"])
def test_valid_ids(good: str) -> None:
    Entry(good, "note", "")


@pytest.mark.parametrize("bad", ["", "_a", "-a", ".a", "~a", "x" * 65, "a b", "ä", "a/b", "a\n"])
def test_invalid_ids(bad: str) -> None:
    with pytest.raises(ValidationError, match="invalid id"):
        Entry(bad, "note", "")


def test_roles_bodies_and_protection_type() -> None:
    for role in ("assistant", "observation", "note", "summary", "receipt"):
        Entry("a", role, "x")
    with pytest.raises(ValidationError, match="invalid role"):
        Entry("a", "user", "x")
    with pytest.raises(ValidationError, match="invalid role"):
        Entry("a", "system", "x")
    with pytest.raises(ValidationError, match="body must be a string"):
        Entry("a", "note", 5)  # type: ignore[arg-type]
    Entry("a", "note", "é" * MAX_BODY_CODEPOINTS)  # the limit is in code points, not bytes
    with pytest.raises(ValidationError, match="code points"):
        Entry("a", "note", "a" * (MAX_BODY_CODEPOINTS + 1))
    with pytest.raises(ValidationError, match="lone surrogate"):
        Entry("a", "note", "bad \ud800")
    with pytest.raises(ValidationError, match="protected must be a bool"):
        Entry("a", "note", "x", 1)  # type: ignore[arg-type]


def test_reserved_namespaces() -> None:
    for i in (
        "t1.request",
        "t2.answer",
        "t2.answer~2",
        "t02.answer",
        "t3.request~x",
        "s4.act",
        "s4.obs~1",
        "s10.rcpt",
    ):
        assert is_reserved_id(i), i
    for i in ("t2.answers", "t.answer", "notes.t2.answer", "s4.action", "task", "s4"):
        assert not is_reserved_id(i), i


# ------------------------------------------------------------ serialisation and limits
def test_serialisation_is_deterministic_and_documented() -> None:
    s = store_with(Entry("b", "observation", 'say "hi"\n'), Entry("a", "note", "é"))
    text = s.snapshot().to_json()
    assert text == (
        '{"format":"clm-context/v1","entries":['
        '{"id":"b","role":"observation","body":"say \\"hi\\"\\n"},'
        '{"id":"a","role":"note","body":"é"}]}'
    )
    assert json.loads(text) == s.snapshot().to_dict()
    assert s.snapshot().serialized_bytes == len(text.encode("utf-8"))
    assert serialize([Entry("x", "assistant", "y", protected=True)]).endswith(
        '"body":"y","protected":true}]}'
    )


def test_entry_count_limit() -> None:
    s = store_with(*(Entry(f"e{i}", "observation", "") for i in range(MAX_ENTRIES)))
    r = s.apply_edits([add("one-more")], expected_revision=1)
    assert r.outcome == "rejected" and "entries (limit 200)" in (r.reason or "")
    assert len(s) == MAX_ENTRIES and s.revision == 1


def test_total_codepoint_limit_with_multibyte_text() -> None:
    body = "日" * MAX_BODY_CODEPOINTS  # 3 bytes per code point
    s = ContextStore()
    # 120,000 code points of 3-byte text = 360,000 bytes: the byte limit is hit first.
    r = s.append([Entry(f"e{i}", "observation", body) for i in range(5)])
    assert not r.ok and "bytes (limit 256000)" in (r.reason or "")
    s = store_with(*(Entry(f"e{i}", "observation", "a" * MAX_BODY_CODEPOINTS) for i in range(5)))
    assert s.snapshot().total_codepoints == MAX_TOTAL_CODEPOINTS
    e = s.apply_edits([add("n", "a")], expected_revision=1)
    assert e.outcome == "rejected" and "code points (limit 120000)" in (e.reason or "")


def test_byte_limit_counts_json_escapes_and_utf8() -> None:
    # Control characters are escaped as \u00XX (6 bytes each) in the canonical form.
    per = 22_000  # 2 x 22,000 x 6 bytes = 264,000 bytes, under the code-point limits
    entries = [Entry(f"e{i}", "observation", "\x01" * per) for i in range(2)]
    assert len(serialize(entries).encode()) > MAX_SERIALIZED_BYTES
    assert not ContextStore().append(entries).ok
    # Just under the limit with 4-byte characters is accepted.
    s = ContextStore()
    n = (MAX_SERIALIZED_BYTES - 200) // 4 // 3
    assert s.append([Entry(f"e{i}", "observation", "😀" * n) for i in range(3)]).ok
    assert s.snapshot().serialized_bytes <= MAX_SERIALIZED_BYTES


# ------------------------------------------------------------ snapshots
def test_snapshots_cannot_change_live_state() -> None:
    s = store_with(Entry("a", "note", "one"))
    snap = s.snapshot()
    d = snap.to_dict()
    d["entries"][0]["body"] = "changed"
    d["entries"].append({"id": "x", "role": "note", "body": "y"})
    with pytest.raises(dataclasses.FrozenInstanceError):
        snap.entries[0].body = "changed"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        snap.entries.append(Entry("x", "note", ""))  # type: ignore[attr-defined]
    assert s.snapshot().to_dict()["entries"] == [{"id": "a", "role": "note", "body": "one"}]
    # A later accepted edit does not alter an earlier snapshot.
    assert s.apply_edits(
        [{"op": "replace", "id": "a", "body": "two"}], expected_revision=1
    ).accepted
    assert snap.get("a").body == "one" and snap.revision == 1  # type: ignore[union-attr]


# ------------------------------------------------------------ transactions
def test_ordered_operations_and_receipt() -> None:
    s = store_with(Entry("o1", "observation", "raw log"), Entry("o2", "observation", "x"))
    r = s.apply_edits(
        [
            add("plan", "step 1"),
            {"op": "replace", "id": "o1", "body": "log summary"},
            {"op": "delete", "ids": ["o2"]},
        ],
        expected_revision=1,
    )
    assert r.outcome == "accepted" and (r.base_revision, r.revision) == (1, 2)
    assert (r.removed, r.added, r.rewritten) == (("o2",), ("plan",), ("o1",))
    snap = s.snapshot()
    assert snap.ids == ("o1", "plan")
    assert snap.get("o1").role == "observation"  # type: ignore[union-attr]  # replace keeps role
    assert snap.get("plan").role == "note"  # type: ignore[union-attr]
    assert r.receipt() == {
        "outcome": "accepted",
        "revision": 2,
        "removed": ["o2"],
        "added": ["plan"],
        "rewritten": ["o1"],
    }
    assert "raw log" not in json.dumps(r.receipt())


def test_typed_operations_are_accepted() -> None:
    s = store_with(Entry("o", "observation", "x"))
    r = s.apply_edits([AddNote("n", "y"), Replace("o", "z"), Delete(("n",))], expected_revision=1)
    assert r.accepted and r.added == () and r.rewritten == ("o",)


def test_later_invalid_operation_rolls_back_everything() -> None:
    s = store_with(Entry("o", "observation", "keep"))
    before = s.snapshot()
    r = s.apply_edits(
        [add("n"), {"op": "replace", "id": "o", "body": "new"}, {"op": "delete", "ids": ["zz"]}],
        expected_revision=1,
    )
    assert r.outcome == "rejected" and r.operation_index == 2
    assert r.reason == "delete: unknown id 'zz'"
    assert s.snapshot() == before and s.revision == 1
    assert r.receipt() == {"outcome": "rejected", "revision": 1, "reason": r.reason, "operation": 2}


@pytest.mark.parametrize(
    ("ops", "reason"),
    [
        ([], "non-empty list"),
        ("delete everything", "non-empty list"),
        ([{"op": "move", "id": "o"}], "unknown op 'move'"),
        ([{"op": "delete", "ids": []}], "non-empty list"),
        ([{"op": "delete", "ids": ["o", "o"]}], "duplicates"),
        ([{"op": "delete", "ids": "o"}], "non-empty list"),
        ([{"op": "replace", "id": "o"}], "missing keys ['body']"),
        ([{"op": "replace", "id": "o", "body": "x", "role": "note"}], "unexpected keys ['role']"),
        ([{"op": "add_note", "id": "n", "body": "x", "protected": True}], "unexpected keys"),
        ([{"op": "add_note", "id": "bad id", "body": "x"}], "invalid id"),
        ([{"op": "add_note", "id": "n", "body": 3}], "body must be a string"),
        ([{"op": "replace", "id": "missing", "body": "x"}], "unknown id 'missing'"),
        ([add("o")], "already exists"),
        ([add("n"), add("n")], "already exists"),
        ([{"op": "delete", "ids": ["o"]}, add("o")], "cannot be reused in the same transaction"),
        ([add("t2.answer")], "reserved runtime namespace"),
        ([add("t2.answer~2")], "reserved runtime namespace"),
        ([add("s3.obs")], "reserved runtime namespace"),
        (["not an object"], "must be an object"),
    ],
)
def test_invalid_transactions_reject(ops: Any, reason: str) -> None:
    s = store_with(Entry("o", "observation", "x"))
    r = s.apply_edits(ops, expected_revision=1)
    assert r.outcome == "rejected" and reason in (r.reason or ""), r.reason
    assert s.revision == 1 and s.snapshot().ids == ("o",)


def test_stale_and_invalid_expected_revisions() -> None:
    s = store_with(Entry("o", "observation", "x"))
    for rev, reason in (
        (0, "stale revision 0"),
        (2, "stale revision 2"),
        (True, "integer"),
        ("1", "integer"),
        (None, "integer"),
    ):
        r = s.apply_edits([add("n")], expected_revision=rev)
        assert r.outcome == "rejected" and reason in (r.reason or "")
    assert s.revision == 1


def test_unchanged_transactions_do_not_create_revisions() -> None:
    s = store_with(Entry("o", "observation", "same"))
    for ops in (
        [{"op": "replace", "id": "o", "body": "same"}],
        [add("tmp"), {"op": "delete", "ids": ["tmp"]}],
    ):
        r = s.apply_edits(ops, expected_revision=1)
        assert r.outcome == "unchanged" and r.revision == 1
        assert r.receipt() == {
            "outcome": "unchanged",
            "revision": 1,
            "removed": [],
            "added": [],
            "rewritten": [],
        }
    assert s.revision == 1


def test_same_transaction_interactions() -> None:
    s = store_with(Entry("o", "observation", "x"))
    # Add then replace the new note: it is reported as added (it did not exist before).
    r = s.apply_edits(
        [add("n", "v1"), {"op": "replace", "id": "n", "body": "v2"}], expected_revision=1
    )
    assert r.accepted and r.added == ("n",) and r.rewritten == ()
    assert s.snapshot().get("n").body == "v2"  # type: ignore[union-attr]
    # Replace twice, ending at the original body: unchanged.
    r = s.apply_edits(
        [{"op": "replace", "id": "o", "body": "y"}, {"op": "replace", "id": "o", "body": "x"}],
        expected_revision=2,
    )
    assert r.outcome == "unchanged"
    # Replace then delete: reported as removed only.
    r = s.apply_edits(
        [{"op": "replace", "id": "o", "body": "y"}, {"op": "delete", "ids": ["o"]}],
        expected_revision=2,
    )
    assert r.accepted and r.removed == ("o",) and r.rewritten == ()
    # A deleted id may be reused in a later transaction (as a note).
    r = s.apply_edits([add("o", "fresh")], expected_revision=3)
    assert r.accepted and r.added == ("o",)
    assert s.snapshot().get("o").role == "note"  # type: ignore[union-attr]


def test_host_append_rules() -> None:
    s = ContextStore()
    assert s.append(
        [Entry("s1.act", "assistant", "thinking"), Entry("s1.obs", "observation", "")]
    ).ok
    assert s.revision == 1  # one revision for the batch
    for bad, reason in (
        ([Entry("s1.act", "assistant", "again")], "already exists"),
        ([Entry("t1.answer", "assistant", "fake")], "lifecycle-owned"),
        ([Entry("x", "note", "y", protected=True)], "turn lifecycle"),
        ([Entry("y", "note", ""), Entry("y", "note", "")], "duplicate id"),
        ([], "nothing to append"),
    ):
        r = s.append(bad)
        assert not r.ok and reason in (r.reason or ""), r.reason
    assert s.revision == 1 and s.snapshot().ids == ("s1.act", "s1.obs")
