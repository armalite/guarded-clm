"""ClmContext's behaviour is pinned: every scenario's transcript must match the stored one exactly.

A transcript is every request the model saw (messages, instructions, tool definitions, settings), every trace
record and how each run ended. The stored transcripts were recorded once and checked against an independent
reference copy of the implementation (docs/results.md). A failure here means the behaviour changed: fix the library,
never the stored transcript (SPEC.md, "Transcript tests").
"""

import json
from pathlib import Path
from typing import Any

import pytest

from guarded_clm import ContextLimits, clm_context
from tests.golden import scenarios

DATA = Path(__file__).parent / "golden" / "data"


def library_factory(settings: scenarios.Settings, on_record: Any) -> scenarios.Stack:
    capability = clm_context(
        budget_tokens=settings.budget_tokens,
        max_steps=settings.max_steps,
        call_cap=settings.call_cap,
        end_over_budget=settings.end_over_budget,
        max_output_chars=settings.max_output_chars,
        limits=ContextLimits(
            max_file_bytes=settings.max_file_bytes,
            max_entries=settings.max_entries,
            max_body_chars=settings.max_body_chars,
            max_total_chars=settings.max_total_chars,
        ),
        on_record=on_record,
    )
    return scenarios.Stack(capabilities=[capability])


def first_difference(expected: Any, actual: Any, path: str = "") -> str | None:
    if type(expected) is not type(actual):
        return f"{path}: expected {type(expected).__name__}, got {type(actual).__name__}"
    if isinstance(expected, dict):
        for key in [*expected, *(k for k in actual if k not in expected)]:
            if key not in expected or key not in actual:
                return f"{path}/{key}: {'missing' if key not in actual else 'unexpected'}"
            found = first_difference(expected[key], actual[key], f"{path}/{key}")
            if found:
                return found
        return None
    if isinstance(expected, list):
        for i, (e, a) in enumerate(zip(expected, actual, strict=False)):
            found = first_difference(e, a, f"{path}[{i}]")
            if found:
                return found
        return f"{path}: {len(expected)} items expected, {len(actual)} found" if len(expected) != len(actual) else None
    if expected != actual:
        if isinstance(expected, str) and isinstance(actual, str):
            at = next((i for i, (x, y) in enumerate(zip(expected, actual, strict=False)) if x != y), None)
            at = min(len(expected), len(actual)) if at is None else at
            return f"{path} at character {at}:\n  expected ...{expected[max(0, at - 120) : at + 120]!r}\n  got ...{actual[max(0, at - 120) : at + 120]!r}"
        return f"{path}: expected {expected!r}, got {actual!r}"
    return None


def test_every_scenario_has_a_golden() -> None:
    stored = sorted(p.stem for p in DATA.glob("*.json"))
    assert stored == sorted(scenarios.SCENARIOS)


@pytest.mark.parametrize("name", sorted(scenarios.SCENARIOS))
def test_scenario_matches_the_stored_transcript(name: str) -> None:
    golden = json.loads((DATA / f"{name}.json").read_text(encoding="utf-8"))
    actual = json.loads(json.dumps(scenarios.SCENARIOS[name](library_factory)))
    difference = first_difference(golden["transcript"], actual)
    assert difference is None, f"{name} differs from the stored transcript: {difference}"
