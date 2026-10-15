"""Record or check the transcript tests' stored transcripts (tests/golden/data/).

    uv run python scripts/record_transcripts.py --check            # compare guarded_clm with the stored transcripts
    uv run python scripts/record_transcripts.py --package guarded_clm --out tests/golden/data

`--package` names any importable implementation exposing `clm_context()` and `ContextLimits` with this library's
signature, so the same scenarios can be recorded from one implementation and checked against another (in separate
environments). Recording is for an approved behaviour change or a new scenario only; never re-record to make a
change pass (SPEC.md, "Transcript tests").
"""

import argparse
import importlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
DEFAULT_DATA = REPO / "tests" / "golden" / "data"


def load_scenarios() -> Any:
    spec = importlib.util.spec_from_file_location("transcript_scenarios", REPO / "tests" / "golden" / "scenarios.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["transcript_scenarios"] = module
    spec.loader.exec_module(module)
    return module


def factory_for(package: str, scenarios: Any) -> Any:
    lib = importlib.import_module(package)

    def factory(settings: Any, on_record: Any) -> Any:
        capability = lib.clm_context(
            budget_tokens=settings.budget_tokens,
            max_steps=settings.max_steps,
            call_cap=settings.call_cap,
            end_over_budget=settings.end_over_budget,
            max_output_chars=settings.max_output_chars,
            limits=lib.ContextLimits(
                max_file_bytes=settings.max_file_bytes,
                max_entries=settings.max_entries,
                max_body_chars=settings.max_body_chars,
                max_total_chars=settings.max_total_chars,
            ),
            on_record=on_record,
        )
        return scenarios.Stack(capabilities=[capability])

    return factory


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", default="guarded_clm")
    parser.add_argument("--out", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--only", action="append", default=[])
    parser.add_argument("--check", action="store_true", help="compare with the stored transcripts, write nothing")
    args = parser.parse_args()
    scenarios = load_scenarios()
    factory = factory_for(args.package, scenarios)
    names = args.only or list(scenarios.SCENARIOS)
    args.out.mkdir(parents=True, exist_ok=True)
    failed = []
    for name in names:
        transcript = json.loads(json.dumps(scenarios.SCENARIOS[name](factory)))
        path = args.out / f"{name}.json"
        if args.check:
            stored = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
            if stored is None or stored["transcript"] != transcript:
                print(f"{name}: differs from the stored transcript", file=sys.stderr)
                failed.append(name)
            continue
        payload = {"scenario": name, "transcript": transcript}
        path.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"{name}: {len(transcript['calls'])} calls, {len(transcript['records'])} records -> {path.name}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
