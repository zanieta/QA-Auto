"""Where the orchestrator gets its test plans from.

A `CaseSource` is anything that can answer two questions:
  - what plan does this key refer to?       (key, name)
  - what cases are in it?                   (id, name, ordered steps)

Today there is one implementation: `FixtureCaseSource`, which reads
`fixtures/sample_plan.json`. When the QMetry shape is locked down, we'll add
`QMetryCaseSource` and orchestrator.py won't need to change.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol


class CaseSource(Protocol):
    async def get_plan(self, plan_key: str) -> dict[str, str]: ...
    async def list_cases(self, plan_key: str) -> list[dict[str, Any]]: ...


FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


class FixtureCaseSource:
    """Reads the static `fixtures/sample_plan.json`.

    The plan_key argument is ignored in fixture mode — there is only one plan.
    """

    def __init__(self, fixture_path: Path | None = None):
        self.fixture_path = fixture_path or (FIXTURES_DIR / "sample_plan.json")

    def _load(self) -> dict[str, Any]:
        return json.loads(self.fixture_path.read_text(encoding="utf-8"))

    async def get_plan(self, plan_key: str) -> dict[str, str]:
        data = self._load()
        plan = data["plan"]
        # If the caller asked for a different key, still return the fixture
        # plan but honor the requested key so the rail shows it correctly.
        return {"key": plan_key or plan["key"], "name": plan["name"]}

    async def list_cases(self, plan_key: str) -> list[dict[str, Any]]:
        return self._load()["cases"]
