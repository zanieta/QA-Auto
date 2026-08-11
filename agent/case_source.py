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

    async def list_cases(
        self, plan_key: str, with_steps: bool = True
    ) -> list[dict[str, Any]]: ...

    async def get_case_steps(
        self, plan_key: str, case_id: str
    ) -> list[dict[str, str]]: ...

    async def get_case_test_data(
        self, plan_key: str, case_id: str
    ) -> list[dict[str, str]]: ...


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

    async def list_cases(
        self, plan_key: str, with_steps: bool = True
    ) -> list[dict[str, Any]]:
        # The fixture already holds every step, so there is nothing to defer —
        # `with_steps` exists for the QMetry source, where steps cost a call
        # each. Fixture cases are always fully loaded.
        cases = self._load()["cases"]
        for case in cases:
            case.setdefault("_steps_loaded", True)
        return cases

    async def get_case_steps(
        self, plan_key: str, case_id: str
    ) -> list[dict[str, str]]:
        cases = await self.list_cases(plan_key)
        match = next((c for c in cases if c["id"] == case_id), None)
        return match["steps"] if match else []

    async def get_case_test_data(
        self, plan_key: str, case_id: str
    ) -> list[dict[str, str]]:
        # Case-level test data is a QMetry parameter table; the fixture has none.
        cases = await self.list_cases(plan_key)
        match = next((c for c in cases if c["id"] == case_id), None)
        return list(match.get("test_data") or []) if match else []
