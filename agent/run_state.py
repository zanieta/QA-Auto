"""RunState — the single source of truth shared with the frontend.

The shape of `.to_dict()` MUST match the contract in FRONTEND.md exactly.
`tests/test_run_state.py` asserts the serialized shape.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Literal

StepStatus = Literal["running", "pass", "fail", "blocked"]
CaseStatus = Literal["queued", "running", "pass", "fail", "blocked"]
RunStatus = Literal["idle", "running", "done"]


@dataclass
class Step:
    action: str
    detail: str
    status: StepStatus = "running"
    evaluation: str | None = None
    duration_seconds: float | None = None
    screenshot_b64: str | None = None
    test_data: str | None = None

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "detail": self.detail,
            "status": self.status,
            "evaluation": self.evaluation,
            "duration_seconds": self.duration_seconds,
            "screenshot_b64": self.screenshot_b64,
            "test_data": self.test_data,
        }


@dataclass
class TestCase:
    id: str
    name: str
    status: CaseStatus = "queued"
    precondition: str | None = None
    test_data: list[dict[str, str]] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)

    __test__ = False  # tell pytest this isn't a test class

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "precondition": self.precondition,
            "test_data": list(self.test_data),
            "steps": [s.to_dict() for s in self.steps],
        }


@dataclass
class Plan:
    key: str
    name: str

    def to_dict(self) -> dict:
        return {"key": self.key, "name": self.name}


@dataclass
class RunState:
    run_id: str
    plan: Plan
    status: RunStatus = "idle"
    test_cases: list[TestCase] = field(default_factory=list)
    _started_at: float | None = None
    _finished_at: float | None = None

    @property
    def elapsed_seconds(self) -> float:
        if self._started_at is None:
            return 0.0
        end = self._finished_at if self._finished_at is not None else time.monotonic()
        return round(end - self._started_at, 2)

    @property
    def summary(self) -> dict:
        total = len(self.test_cases)
        passed = sum(1 for c in self.test_cases if c.status == "pass")
        failed = sum(1 for c in self.test_cases if c.status == "fail")
        blocked = sum(1 for c in self.test_cases if c.status == "blocked")
        return {"total": total, "passed": passed, "failed": failed, "blocked": blocked}

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "plan": self.plan.to_dict(),
            "status": self.status,
            "elapsed_seconds": self.elapsed_seconds,
            "summary": self.summary,
            "test_cases": [c.to_dict() for c in self.test_cases],
        }

    # Mutation helpers — called by orchestrator after each transition
    def start_run(self) -> None:
        self.status = "running"
        self._started_at = time.monotonic()

    def add_case(self, case: TestCase) -> None:
        self.test_cases.append(case)

    def start_case(self, case_id: str) -> TestCase:
        case = self._find_case(case_id)
        case.status = "running"
        return case

    def add_step(self, case_id: str, step: Step) -> Step:
        case = self._find_case(case_id)
        case.steps.append(step)
        return step

    def resolve_step(
        self,
        case_id: str,
        step_index: int,
        status: StepStatus,
        evaluation: str | None,
        duration_seconds: float,
        screenshot_b64: str | None = None,
    ) -> None:
        case = self._find_case(case_id)
        step = case.steps[step_index]
        step.status = status
        step.evaluation = evaluation
        step.duration_seconds = round(duration_seconds, 2)
        step.screenshot_b64 = screenshot_b64

    def resolve_case(self, case_id: str, status: CaseStatus) -> None:
        self._find_case(case_id).status = status

    def finish(self) -> None:
        self.status = "done"
        self._finished_at = time.monotonic()

    def _find_case(self, case_id: str) -> TestCase:
        for c in self.test_cases:
            if c.id == case_id:
                return c
        raise KeyError(f"Test case {case_id!r} not in run state")


def new_run_state(plan_key: str, plan_name: str = "") -> RunState:
    """Build a fresh RunState for a plan."""
    return RunState(
        run_id=f"run-{uuid.uuid4().hex[:8]}",
        plan=Plan(key=plan_key, name=plan_name or plan_key),
    )
