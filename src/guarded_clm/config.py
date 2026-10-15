"""Validated configuration (SPEC §2)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


class ConfigError(ValueError):
    """A configuration value has the wrong type or is outside its allowed range."""


def _int(name: str, value: Any, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{name} must be an integer, got {type(value).__name__}")
    if value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}, got {value}")
    return value


def _ratio(name: str, value: Any, *, low: float, high: float | None, low_open: bool) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigError(f"{name} must be a number, got {type(value).__name__}")
    v = float(value)
    if not math.isfinite(v):
        raise ConfigError(f"{name} must be finite, got {value}")
    if (v <= low) if low_open else (v < low):
        raise ConfigError(f"{name} must be {'>' if low_open else '>='} {low}, got {value}")
    if high is not None and v >= high:
        raise ConfigError(f"{name} must be < {high}, got {value}")
    return v


@dataclass(frozen=True, slots=True)
class GuardedConfig:
    """Library configuration. Only ``budget_tokens`` is required.

    - ``budget_tokens``: the whole-request token budget (> ``reserve_tokens``).
    - ``reserve_tokens``: tokens held back for recovery; the working limit is
      ``budget_tokens - reserve_tokens``.
    - ``pressure_ratio``: pressure starts at this fraction of the budget; it must fall at or
      below the working limit, so pressure is signalled before the limit is reached.
    - ``initial_new_text_ratio``: the shared estimator's starting characters-per-token ratio
      for appended text (planned use, SPEC §9.3).
    - ``guard_ratio_cap``: the output guard's characters-per-token ratio is at most this value
      (a cap, not a floor; planned use, SPEC §9.4).
    - ``guard_margin``: the guard's relative safety margin on added text (planned use).
    - ``protect_turns``: earlier requests and final answers become protected entries.
    """

    budget_tokens: int
    reserve_tokens: int = 2048
    pressure_ratio: float = 0.70
    initial_new_text_ratio: float = 2.5
    guard_ratio_cap: float = 1.5
    guard_margin: float = 0.10
    protect_turns: bool = True

    def __post_init__(self) -> None:
        budget = _int("budget_tokens", self.budget_tokens, 1)
        reserve = _int("reserve_tokens", self.reserve_tokens, 0)
        if reserve >= budget:
            raise ConfigError(
                f"reserve_tokens ({reserve}) must be smaller than budget_tokens ({budget})"
            )
        pressure = _ratio("pressure_ratio", self.pressure_ratio, low=0.0, high=1.0, low_open=True)
        if pressure * budget > budget - reserve:
            raise ConfigError(
                f"pressure point ({pressure} x {budget}) is above the working limit "
                f"({budget} - {reserve}); pressure would never precede the limit"
            )
        new_ratio = _ratio(
            "initial_new_text_ratio", self.initial_new_text_ratio, low=0.0, high=None, low_open=True
        )
        cap = _ratio("guard_ratio_cap", self.guard_ratio_cap, low=0.0, high=None, low_open=True)
        margin = _ratio("guard_margin", self.guard_margin, low=0.0, high=1.0, low_open=False)
        if not isinstance(self.protect_turns, bool):
            raise ConfigError(
                f"protect_turns must be a bool, got {type(self.protect_turns).__name__}"
            )
        # Store normalised floats (an int such as 1 for a ratio becomes 1.0).
        object.__setattr__(self, "pressure_ratio", pressure)
        object.__setattr__(self, "initial_new_text_ratio", new_ratio)
        object.__setattr__(self, "guard_ratio_cap", cap)
        object.__setattr__(self, "guard_margin", margin)

    @property
    def working_limit_tokens(self) -> int:
        """``budget_tokens - reserve_tokens`` (planned use: SPEC §9.5)."""
        return self.budget_tokens - self.reserve_tokens
