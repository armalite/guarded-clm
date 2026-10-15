"""Package boundary: the core is provider-neutral and dependency-free."""

from __future__ import annotations

import subprocess
import sys

import guarded_clm


def test_core_imports_no_provider_or_framework() -> None:
    code = (
        "import sys, guarded_clm; "
        "bad = [m for m in sys.modules if m.split('.')[0] in "
        "('anthropic', 'pydantic_ai', 'pydantic', 'openai', 'httpx')]; print(bad)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


def test_public_api_exports() -> None:
    for name in guarded_clm.__all__:
        assert hasattr(guarded_clm, name), name
    assert guarded_clm.__version__ == "0.1.0.dev0"
