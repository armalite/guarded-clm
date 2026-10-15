from __future__ import annotations

import math
from typing import Any

import pytest

from guarded_clm import ConfigError, GuardedConfig


def test_defaults() -> None:
    c = GuardedConfig(budget_tokens=16_000)
    assert (c.reserve_tokens, c.pressure_ratio, c.initial_new_text_ratio) == (2048, 0.70, 2.5)
    assert (c.guard_ratio_cap, c.guard_margin, c.protect_turns) == (1.5, 0.10, True)
    assert c.working_limit_tokens == 16_000 - 2048


def test_budget_is_required() -> None:
    with pytest.raises(TypeError):
        GuardedConfig()  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"budget_tokens": 0}, "budget_tokens must be >= 1"),
        ({"budget_tokens": 8000.0}, "budget_tokens must be an integer"),
        ({"budget_tokens": True}, "budget_tokens must be an integer"),
        ({"budget_tokens": 8000, "reserve_tokens": -1}, "reserve_tokens must be >= 0"),
        ({"budget_tokens": 2048}, "must be smaller than budget_tokens"),
        ({"budget_tokens": 8000, "pressure_ratio": 0}, "pressure_ratio must be > 0"),
        ({"budget_tokens": 8000, "pressure_ratio": 1.0}, "pressure_ratio must be < 1"),
        ({"budget_tokens": 8000, "pressure_ratio": math.nan}, "must be finite"),
        ({"budget_tokens": 8000, "pressure_ratio": "0.7"}, "must be a number"),
        ({"budget_tokens": 4000}, "above the working limit"),
        ({"budget_tokens": 8000, "initial_new_text_ratio": 0.0}, "must be > 0"),
        ({"budget_tokens": 8000, "guard_ratio_cap": -1.5}, "must be > 0"),
        ({"budget_tokens": 8000, "guard_ratio_cap": math.inf}, "must be finite"),
        ({"budget_tokens": 8000, "guard_margin": -0.1}, "must be >= 0"),
        ({"budget_tokens": 8000, "guard_margin": 1.0}, "must be < 1"),
        ({"budget_tokens": 8000, "guard_margin": False}, "must be a number"),
        ({"budget_tokens": 8000, "protect_turns": 1}, "protect_turns must be a bool"),
    ],
)
def test_invalid_values_are_rejected(kwargs: dict[str, Any], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        GuardedConfig(**kwargs)


def test_boundaries_accepted_and_normalised() -> None:
    # Pressure exactly at the working limit is allowed; zero reserve and zero margin are allowed.
    c = GuardedConfig(budget_tokens=10_000, reserve_tokens=3_000, pressure_ratio=0.7)
    assert c.working_limit_tokens == 7_000
    c = GuardedConfig(budget_tokens=1, reserve_tokens=0, guard_margin=0, guard_ratio_cap=2)
    assert c.guard_margin == 0.0 and isinstance(c.guard_margin, float)
    assert c.guard_ratio_cap == 2.0 and isinstance(c.guard_ratio_cap, float)


def test_config_is_immutable() -> None:
    c = GuardedConfig(budget_tokens=8000)
    with pytest.raises(AttributeError):
        c.budget_tokens = 1  # type: ignore[misc]
