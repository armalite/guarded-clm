"""``GuardedContext``: the turn lifecycle around the authoritative store (SPEC §6).

Turn records:

- the first user request is the **task anchor**. It is kept outside the working context, is
  immutable, and is never duplicated as ``t1.request``;
- the **current request** of a later turn is host-owned and also kept outside the working
  context while its turn is active, so no edit can reach it;
- at the first request preparation of turn N+1, the runtime **materialises** the records of
  turn N into the working context, in one revision: ``tN.request`` (for N >= 2, role
  ``observation``) and then ``tN.answer`` (role ``assistant``). They are protected when
  ``protect_turns`` is on.

``prepare_request()`` is the hook the request builder (planned, SPEC §9.1) will call before
building every request. It is idempotent: the materialisation happens once, and preparing again
returns the same state without a new revision.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .config import GuardedConfig
from .edits import EditResult
from .entries import Entry, body_problem
from .store import ChangeResult, ContextStore, Snapshot

REQUEST_ROLE = "observation"
ANSWER_ROLE = "assistant"


class LifecycleError(RuntimeError):
    """A lifecycle method was called in the wrong state (a host programming error)."""


class TurnState(Enum):
    NOT_STARTED = "not_started"
    ACTIVE = "active"  # the turn's request exists; no final answer yet
    ANSWERED = "answered"  # the final answer is recorded; the next turn may start


@dataclass(frozen=True, slots=True)
class RequestState:
    """What the request builder needs for one request (SPEC §9.1)."""

    turn: int
    task: str
    current_request: str | None  # None on turn 1: the first request is the task itself
    snapshot: Snapshot


@dataclass(frozen=True, slots=True)
class PrepareResult:
    ok: bool
    state: RequestState | None = None
    reason: str | None = None
    materialised: tuple[str, ...] = ()  # turn-record IDs added by this call (empty on repeats)


def turn_request_id(turn: int) -> str:
    return f"t{turn}.request"


def turn_answer_id(turn: int) -> str:
    return f"t{turn}.answer"


class GuardedContext:
    """The library's main object: configuration, the authoritative store and the turn
    lifecycle. Its methods are trusted host APIs (see ``store`` for the trust boundary)."""

    def __init__(self, config: GuardedConfig) -> None:
        if not isinstance(config, GuardedConfig):
            raise TypeError("config must be a GuardedConfig")
        self._config = config
        self._store = ContextStore()
        self._state = TurnState.NOT_STARTED
        self._turn = 0
        self._task: str | None = None
        self._current_request: str | None = None
        self._answer: str | None = None
        self._pending: tuple[Entry, ...] = ()
        self._prepared = False

    # ------------------------------------------------------------ read-only views
    @property
    def config(self) -> GuardedConfig:
        return self._config

    @property
    def turn(self) -> int:
        return self._turn

    @property
    def state(self) -> TurnState:
        return self._state

    @property
    def task(self) -> str | None:
        return self._task

    @property
    def current_request(self) -> str | None:
        return self._current_request

    @property
    def revision(self) -> int:
        return self._store.revision

    def snapshot(self) -> Snapshot:
        return self._store.snapshot()

    # ------------------------------------------------------------ lifecycle
    def start_turn(self, request: str) -> int:
        """Begin the next turn with a user request; returns the new turn number.

        Turn 1's request becomes the task anchor. A later turn may start only after the previous
        turn's final answer is recorded. The previous turn's records are materialised at the
        next ``prepare_request()``.
        """
        if not isinstance(request, str) or not request.strip():
            raise ValueError("a request must be a non-empty string")
        if self._turn == 0:
            # The task anchor is never an entry, so the body-length limit does not apply.
            try:
                request.encode("utf-8")
            except UnicodeEncodeError:
                raise ValueError("the request contains a lone surrogate") from None
            self._task = request
        elif self._state is not TurnState.ANSWERED:
            raise LifecycleError(
                f"turn {self._turn} has no recorded final answer; it cannot be followed yet"
            )
        else:
            problem = body_problem(request)
            if problem:
                # A later request becomes a working-context entry, so it must be a valid body.
                raise ValueError(f"request cannot become a turn record: {problem}")
            assert self._answer is not None
            records = []
            if self._turn >= 2:
                assert self._current_request is not None
                records.append(
                    self._record(turn_request_id(self._turn), REQUEST_ROLE, self._current_request)
                )
            records.append(self._record(turn_answer_id(self._turn), ANSWER_ROLE, self._answer))
            self._pending = tuple(records)
            self._current_request = request
        self._turn += 1
        self._state = TurnState.ACTIVE
        self._answer = None
        self._prepared = False
        return self._turn

    def prepare_request(self) -> PrepareResult:
        """The request-building hook (idempotent).

        On the first call of a turn, it materialises the previous turn's records in one
        revision. If they would break a structural limit, nothing changes, the result is a
        failure, and the records stay pending. Later calls only return the current state.
        """
        if self._state is TurnState.NOT_STARTED:
            raise LifecycleError("no turn has started")
        if self._state is TurnState.ANSWERED:
            raise LifecycleError(f"turn {self._turn} is answered; start the next turn first")
        added: tuple[str, ...] = ()
        if self._pending:
            change = self._store._append_records(self._pending)
            if not change.ok:
                return PrepareResult(False, reason=f"turn records do not fit: {change.reason}")
            added = change.added
            self._pending = ()
        self._prepared = True
        assert self._task is not None
        state = RequestState(self._turn, self._task, self._current_request, self._store.snapshot())
        return PrepareResult(True, state, materialised=added)

    def submit_answer(self, answer: str) -> None:
        """Record the current turn's final answer, exactly once.

        Only this call creates a final-answer record; intermediate assistant output appended
        as runtime entries never becomes one.
        """
        self._require_prepared("submit an answer")
        if not isinstance(answer, str):
            raise ValueError("an answer must be a string")
        problem = body_problem(answer)
        if problem:
            raise ValueError(f"answer cannot become a turn record: {problem}")
        self._answer = answer
        self._state = TurnState.ANSWERED

    # ------------------------------------------------------------ changes within a turn
    def apply_edits(self, operations: Any, *, expected_revision: Any) -> EditResult:
        """Apply a model edit transaction (SPEC §4) to the working context."""
        self._require_prepared("apply edits")
        return self._store.apply_edits(operations, expected_revision=expected_revision)

    def append_runtime_entries(self, entries: Sequence[Entry]) -> ChangeResult:
        """Append host-created runtime entries (for example ``sN.act``) in one revision."""
        self._require_prepared("append runtime entries")
        return self._store.append(entries)

    # ------------------------------------------------------------ internals
    def _record(self, entry_id: str, role: str, body: str) -> Entry:
        return Entry(entry_id, role, body, protected=self._config.protect_turns)

    def _require_prepared(self, action: str) -> None:
        if self._state is TurnState.ANSWERED:
            raise LifecycleError(f"cannot {action}: turn {self._turn} is already answered")
        if self._state is not TurnState.ACTIVE or not self._prepared:
            raise LifecycleError(f"cannot {action} before the turn's first prepare_request()")
