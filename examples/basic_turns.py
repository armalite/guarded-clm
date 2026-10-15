"""A model-free walk through the guarded-clm core API.

Run: uv run python examples/basic_turns.py

There is no model here: the edits a model would request through a tool call are written out
by hand, to show what the runtime accepts and rejects.
"""

from __future__ import annotations

from guarded_clm import GuardedConfig, GuardedContext


def main() -> list[str]:
    log: list[str] = []
    ctx = GuardedContext(GuardedConfig(budget_tokens=32_000))

    # Turn 1. The first request becomes the task anchor (it is not a working-context entry).
    ctx.start_turn("Investigate why the nightly export failed and recommend a fix.")
    state = ctx.prepare_request()
    assert state.ok and state.state is not None
    rev = state.state.snapshot.revision

    # The model adds a note, then revises it in a later edit.
    r = ctx.apply_edits(
        [{"op": "add_note", "id": "findings", "body": "Export job exited with code 3."}],
        expected_revision=rev,
    )
    log.append(f"add note: {r.outcome}, revision {r.revision}")
    r = ctx.apply_edits(
        [
            {
                "op": "replace",
                "id": "findings",
                "body": "Exit code 3 = disk quota exceeded on the export volume.",
            }
        ],
        expected_revision=r.revision,
    )
    log.append(f"revise note: {r.outcome}, rewritten {list(r.rewritten)}")

    # The turn ends with a final answer.
    ctx.submit_answer("Raise the export volume quota and add a free-space check before export.")

    # Turn 2. Preparing the first request of the turn materialises turn 1's answer.
    ctx.start_turn("Also check whether last week's exports were complete.")
    prep = ctx.prepare_request()
    assert prep.ok and prep.state is not None
    log.append(f"materialised: {list(prep.materialised)}")
    again = ctx.prepare_request()  # idempotent: nothing new, same revision
    assert again.state is not None and again.state.snapshot.revision == prep.state.snapshot.revision
    snap = prep.state.snapshot
    log.append(f"t1.answer protected: {snap.get('t1.answer').protected}")  # type: ignore[union-attr]

    # Deleting protected history is rejected as a whole transaction.
    r = ctx.apply_edits(
        [{"op": "delete", "ids": ["findings"]}, {"op": "delete", "ids": ["t1.answer"]}],
        expected_revision=snap.revision,
    )
    log.append(f"delete protected: {r.outcome} ({r.reason})")

    # An edit to unrelated working context succeeds.
    r = ctx.apply_edits([{"op": "delete", "ids": ["findings"]}], expected_revision=snap.revision)
    log.append(f"delete note: {r.outcome}, removed {list(r.removed)}")
    log.append(f"final context: {ctx.snapshot().to_json()}")
    return log


if __name__ == "__main__":
    for line in main():
        print(line)
