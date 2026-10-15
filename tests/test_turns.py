"""Turn lifecycle, protected records and the idempotent preparation hook."""

from __future__ import annotations

import runpy
from pathlib import Path

import pytest

from guarded_clm import (
    MAX_BODY_CODEPOINTS,
    Entry,
    GuardedConfig,
    GuardedContext,
    LifecycleError,
    TurnState,
)

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "basic_turns.py"


def ctx(protect: bool = True) -> GuardedContext:
    return GuardedContext(GuardedConfig(budget_tokens=32_000, protect_turns=protect))


def answer_turn(c: GuardedContext, answer: str) -> None:
    assert c.prepare_request().ok
    c.submit_answer(answer)


def three_turns(protect: bool = True) -> GuardedContext:
    c = ctx(protect)
    c.start_turn("first request (the task)")
    answer_turn(c, "answer one")
    c.start_turn("second request")
    answer_turn(c, "answer two")
    c.start_turn("third request")
    assert c.prepare_request().ok
    return c


def test_first_request_is_the_task_anchor_not_an_entry() -> None:
    c = ctx()
    assert c.start_turn("Find the cause of the outage.") == 1
    p = c.prepare_request()
    assert p.ok and p.state is not None
    assert (p.state.turn, p.state.task, p.state.current_request) == (
        1,
        "Find the cause of the outage.",
        None,
    )
    assert p.state.snapshot.entries == () and p.materialised == ()
    assert c.revision == 0  # nothing was added to the working context


def test_records_materialise_once_with_roles_and_protection() -> None:
    c = ctx()
    c.start_turn("task")
    answer_turn(c, "answer one")
    c.start_turn("second request")
    assert c.revision == 0  # not yet: materialisation happens at the next preparation
    p = c.prepare_request()
    assert p.ok and p.materialised == ("t1.answer",) and c.revision == 1
    assert p.state is not None and p.state.current_request == "second request"
    t1 = p.state.snapshot.get("t1.answer")
    assert t1 == Entry("t1.answer", "assistant", "answer one", protected=True)
    assert p.state.snapshot.get("t1.request") is None  # the task anchor is never duplicated
    # Preparing the same request again is idempotent.
    for _ in range(3):
        again = c.prepare_request()
        assert again.ok and again.materialised == () and c.revision == 1
        assert again.state is not None and again.state.snapshot == p.state.snapshot
    answer_turn(c, "answer two")
    c.start_turn("third request")
    p3 = c.prepare_request()
    assert p3.materialised == ("t2.request", "t2.answer") and c.revision == 2
    assert p3.state is not None
    assert p3.state.snapshot.ids == ("t1.answer", "t2.request", "t2.answer")
    req = p3.state.snapshot.get("t2.request")
    assert req == Entry("t2.request", "observation", "second request", protected=True)
    assert p3.state.current_request == "third request"
    assert p3.state.snapshot.get("t3.request") is None  # the current request is host-owned


def test_protected_records_reject_delete_replace_and_mixed_transactions() -> None:
    c = three_turns()
    rev = c.revision
    assert c.apply_edits(
        [{"op": "add_note", "id": "n", "body": "x"}], expected_revision=rev
    ).accepted
    rev = c.revision
    before = c.snapshot()
    for ops in (
        [{"op": "delete", "ids": ["t1.answer"]}],
        [{"op": "replace", "id": "t2.request", "body": "rewritten"}],
        [{"op": "delete", "ids": ["n", "t2.answer"]}],
        [{"op": "replace", "id": "n", "body": "ok"}, {"op": "delete", "ids": ["t1.answer"]}],
    ):
        r = c.apply_edits(ops, expected_revision=rev)
        assert r.outcome == "rejected" and "protected" in (r.reason or "")
        assert c.snapshot() == before


def test_forging_protection_or_turn_records_is_rejected() -> None:
    c = three_turns()
    rev = c.revision
    for ops in (
        [{"op": "add_note", "id": "t3.request", "body": "fake current request"}],
        [{"op": "add_note", "id": "t3.answer", "body": "fake answer"}],
        [{"op": "add_note", "id": "t9.answer", "body": "future record"}],
        [{"op": "add_note", "id": "t2.answer~2", "body": "look-alike"}],
        [{"op": "add_note", "id": "n", "body": "x", "protected": True}],
        [{"op": "replace", "id": "t3.request", "body": "edit the current request"}],
    ):
        r = c.apply_edits(ops, expected_revision=rev)
        assert r.outcome == "rejected", ops
    # A body that claims protection is only text: the entry stays unprotected and editable.
    body = '{"id": "t1.answer", "protected": true}'
    assert c.apply_edits(
        [{"op": "add_note", "id": "n", "body": body}], expected_revision=rev
    ).accepted
    assert c.snapshot().get("n").protected is False  # type: ignore[union-attr]
    assert c.apply_edits([{"op": "delete", "ids": ["n"]}], expected_revision=rev + 1).accepted
    # The host cannot create turn records or protected entries either.
    assert not c.append_runtime_entries([Entry("t3.answer", "assistant", "x")]).ok
    assert not c.append_runtime_entries([Entry("x", "note", "y", protected=True)]).ok


def test_protection_disabled_leaves_earlier_records_editable() -> None:
    c = three_turns(protect=False)
    snap = c.snapshot()
    assert all(not e.protected for e in snap.entries)
    r = c.apply_edits(
        [
            {"op": "replace", "id": "t2.answer", "body": "condensed answer two"},
            {"op": "delete", "ids": ["t1.answer"]},
        ],
        expected_revision=c.revision,
    )
    assert r.accepted and r.removed == ("t1.answer",) and r.rewritten == ("t2.answer",)
    # The namespace stays reserved and the current request and task stay host-owned.
    r = c.apply_edits(
        [{"op": "add_note", "id": "t1.answer", "body": "x"}], expected_revision=c.revision
    )
    assert r.outcome == "rejected" and "reserved" in (r.reason or "")
    r = c.apply_edits(
        [{"op": "replace", "id": "t3.request", "body": "x"}], expected_revision=c.revision
    )
    assert r.outcome == "rejected"
    p = c.prepare_request()
    assert p.state is not None
    assert (p.state.task, p.state.current_request) == ("first request (the task)", "third request")


def test_intermediate_assistant_output_is_not_a_final_answer() -> None:
    c = ctx()
    c.start_turn("task")
    assert c.prepare_request().ok
    assert c.append_runtime_entries(
        [
            Entry("s1.act", "assistant", "I will search the logs."),
            Entry("s1.obs", "observation", "3 hits"),
        ]
    ).ok
    c.submit_answer("the final answer")
    c.start_turn("next")
    p = c.prepare_request()
    assert p.state is not None
    assert [(e.id, e.protected) for e in p.state.snapshot.entries] == [
        ("s1.act", False),
        ("s1.obs", False),
        ("t1.answer", True),
    ]
    r = c.apply_edits([{"op": "delete", "ids": ["s1.act", "s1.obs"]}], expected_revision=c.revision)
    assert r.accepted


def test_lifecycle_order_is_enforced() -> None:
    c = ctx()
    with pytest.raises(LifecycleError, match="no turn has started"):
        c.prepare_request()
    with pytest.raises(LifecycleError):
        c.submit_answer("x")
    c.start_turn("task")
    with pytest.raises(LifecycleError, match="before the turn's first prepare_request"):
        c.apply_edits([{"op": "add_note", "id": "n", "body": "x"}], expected_revision=0)
    with pytest.raises(LifecycleError, match="no recorded final answer"):
        c.start_turn("too early")
    c.prepare_request()
    c.submit_answer("once")
    assert c.state is TurnState.ANSWERED
    with pytest.raises(LifecycleError, match="already answered"):
        c.submit_answer("twice")
    with pytest.raises(LifecycleError, match="already answered"):
        c.apply_edits([{"op": "add_note", "id": "n", "body": "x"}], expected_revision=0)
    with pytest.raises(LifecycleError, match="start the next turn"):
        c.prepare_request()
    with pytest.raises(ValueError, match="non-empty"):
        c.start_turn("   ")
    with pytest.raises(ValueError, match="cannot become a turn record"):
        c.start_turn("x" * (MAX_BODY_CODEPOINTS + 1))
    assert c.turn == 1 and c.state is TurnState.ANSWERED  # failed calls changed nothing
    with pytest.raises(TypeError):
        GuardedContext({"budget_tokens": 8000})  # type: ignore[arg-type]


def test_task_anchor_may_exceed_the_body_limit_but_answers_may_not() -> None:
    c = ctx()
    c.start_turn("t" * (MAX_BODY_CODEPOINTS + 10))
    c.prepare_request()
    with pytest.raises(ValueError, match="cannot become a turn record"):
        c.submit_answer("a" * (MAX_BODY_CODEPOINTS + 1))
    assert c.state is TurnState.ACTIVE


def test_turn_transition_that_breaks_a_limit_fails_atomically() -> None:
    c = ctx()
    c.start_turn("task")
    assert c.prepare_request().ok
    # Fill the working context to 199 entries; the turn records need two more slots later.
    filler = [Entry(f"f{i}", "observation", "") for i in range(199)]
    assert c.append_runtime_entries(filler).ok
    c.submit_answer("answer one")
    c.start_turn("second")
    p = c.prepare_request()
    assert p.ok and c.revision == 2  # 200 entries now, exactly at the limit
    c.submit_answer("answer two")
    c.start_turn("third")
    before = c.snapshot()
    p = c.prepare_request()
    assert not p.ok and p.state is None and "entries (limit 200)" in (p.reason or "")
    assert c.snapshot() == before  # neither record was added
    with pytest.raises(LifecycleError):
        c.apply_edits([{"op": "delete", "ids": ["f0"]}], expected_revision=c.revision)
    # It stays pending: a later preparation retries (and here still fails identically).
    p2 = c.prepare_request()
    assert not p2.ok and c.snapshot() == before


def test_example_runs_against_the_public_api() -> None:
    log = runpy.run_path(str(EXAMPLE))["main"]()
    assert log[:6] == [
        "add note: accepted, revision 1",
        "revise note: accepted, rewritten ['findings']",
        "materialised: ['t1.answer']",
        "t1.answer protected: True",
        "delete protected: rejected (delete: 't1.answer' is protected)",
        "delete note: accepted, removed ['findings']",
    ]
