"""guarded-clm: model-controlled working context with validated edits and protected turns.

The model manages editable working context. The runtime validates edits, protects designated
conversation records and enforces the configured budget. See SPEC.md for what is implemented
and what is planned.
"""

from .config import ConfigError, GuardedConfig
from .context import (
    ANSWER_ROLE,
    REQUEST_ROLE,
    GuardedContext,
    LifecycleError,
    PrepareResult,
    RequestState,
    TurnState,
    turn_answer_id,
    turn_request_id,
)
from .edits import AddNote, Delete, EditResult, Operation, OperationError, Replace, parse_operation
from .entries import (
    FORMAT,
    MAX_BODY_CODEPOINTS,
    MAX_ENTRIES,
    MAX_SERIALIZED_BYTES,
    MAX_TOTAL_CODEPOINTS,
    ROLES,
    Entry,
    ValidationError,
    is_reserved_id,
    serialize,
)
from .store import ChangeResult, ContextStore, Snapshot

__version__ = "0.1.0.dev0"

__all__ = [
    "ANSWER_ROLE",
    "FORMAT",
    "MAX_BODY_CODEPOINTS",
    "MAX_ENTRIES",
    "MAX_SERIALIZED_BYTES",
    "MAX_TOTAL_CODEPOINTS",
    "REQUEST_ROLE",
    "ROLES",
    "AddNote",
    "ChangeResult",
    "ConfigError",
    "ContextStore",
    "Delete",
    "EditResult",
    "Entry",
    "GuardedConfig",
    "GuardedContext",
    "LifecycleError",
    "Operation",
    "OperationError",
    "PrepareResult",
    "Replace",
    "RequestState",
    "Snapshot",
    "TurnState",
    "ValidationError",
    "__version__",
    "is_reserved_id",
    "parse_operation",
    "serialize",
    "turn_answer_id",
    "turn_request_id",
]
