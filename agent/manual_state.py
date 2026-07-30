"""Manual test-session state — the contract for the Manual console view.

Separate from RunState (which is the *agent* run contract). Holds, per test
case in a cycle, a tester's hand-entered mark (pass/fail/blocked + note +
flagged failing steps) and any per-case agent run that was triggered.

Marks are held in memory keyed by plan, and snapshotted to
`manual_sessions/<plan>.json` so a server restart does not lose them. The cases
and steps themselves always come live from a CaseSource; the stored marks are
overlaid on top each time a session is built.

The QMetry execution id needed to write results back lives on ManualCase but is
NEVER serialized to the browser.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.run_state import Plan

MANUAL_DIR = Path(__file__).resolve().parent.parent / "manual_sessions"

ManualStatus = str  # "unmarked" | "pass" | "fail" | "blocked"
AgentStatus = str | None  # None | "running" | "pass" | "fail" | "blocked"

_UNSET: Any = object()  # sentinel: "don't touch agent_steps"


@dataclass
class ManualMark:
    status: ManualStatus = "unmarked"
    comment: str = ""
    failed_steps: list[int] = field(default_factory=list)
    step_marks: dict[str, dict] = field(default_factory=dict)
    agent_status: AgentStatus = None
    agent_run_id: str | None = None
    agent_steps: list[int] | None = None  # indices the last agent run covered; None = all
    agent_note: str = ""  # latest agent-run summary; tester comment stays separate
    pushed_to_qmetry: bool = False
    login_username: str = ""  # per-case login account; "" = use the .env default
    login_password: str = ""  # persisted to disk only — never serialized to the browser

    def to_dict(self, include_secrets: bool = False) -> dict:
        d = {
            "status": self.status,
            "comment": self.comment,
            "failed_steps": list(self.failed_steps),
            "step_marks": {k: dict(v) for k, v in self.step_marks.items()},
            "agent_status": self.agent_status,
            "agent_run_id": self.agent_run_id,
            "agent_steps": list(self.agent_steps) if self.agent_steps is not None else None,
            "agent_note": self.agent_note,
            "pushed_to_qmetry": self.pushed_to_qmetry,
            "login_username": self.login_username,
            "has_password": bool(self.login_password),
        }
        if include_secrets:
            d["login_password"] = self.login_password
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ManualMark":
        raw_steps = d.get("agent_steps")
        return cls(
            status=d.get("status", "unmarked"),
            comment=d.get("comment", ""),
            failed_steps=list(d.get("failed_steps", [])),
            step_marks={k: dict(v) for k, v in d.get("step_marks", {}).items()},
            agent_status=d.get("agent_status"),
            agent_run_id=d.get("agent_run_id"),
            agent_steps=list(raw_steps) if raw_steps is not None else None,
            agent_note=d.get("agent_note", ""),
            pushed_to_qmetry=d.get("pushed_to_qmetry", False),
            login_username=d.get("login_username", ""),
            login_password=d.get("login_password", ""),
        )


@dataclass
class ManualCase:
    id: str
    name: str
    steps: list[dict]
    mark: ManualMark = field(default_factory=ManualMark)
    execution_id: int | None = None  # server-side only — never serialized
    execution_cycle_id: str | None = None  # server-side only — never serialized
    tc_id: str | None = None  # server-side only — for create-mode execution
    version_no: int = 1       # server-side only
    precondition: str = ""

    __test__ = False  # not a pytest class

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "steps": [
                {"action": s.get("action", ""), "expected": s.get("expected", "")}
                for s in self.steps
            ],
            "precondition": self.precondition,
            "manual": self.mark.to_dict(),
        }


@dataclass
class ManualSession:
    plan: Plan
    qmetry_configured: bool
    cases: list[ManualCase]

    __test__ = False

    @property
    def summary(self) -> dict:
        def count(status: str) -> int:
            return sum(1 for c in self.cases if c.mark.status == status)

        return {
            "total": len(self.cases),
            "passed": count("pass"),
            "failed": count("fail"),
            "blocked": count("blocked"),
            "unmarked": count("unmarked"),
            "pushed": sum(1 for c in self.cases if c.mark.pushed_to_qmetry),
        }

    def to_dict(self) -> dict:
        return {
            "plan": self.plan.to_dict(),
            "qmetry_configured": self.qmetry_configured,
            "cases": [c.to_dict() for c in self.cases],
            "summary": self.summary,
        }

    def find_case(self, case_id: str) -> ManualCase:
        for c in self.cases:
            if c.id == case_id:
                return c
        raise KeyError(f"Manual case {case_id!r} not in session")


def derive_case_status(step_marks: dict[str, dict]) -> str:
    """Derive the case-level status from per-step marks: fail > blocked > pass;
    no marks (or only skips) -> "unmarked"."""
    statuses = [sm.get("status") for sm in step_marks.values()]
    if "fail" in statuses:
        return "fail"
    if "blocked" in statuses:
        return "blocked"
    if "pass" in statuses:
        return "pass"
    return "unmarked"


def compose_comment(case: ManualCase) -> str:
    """Build the QMetry comment from the note plus one line per marked step."""
    mark = case.mark
    lines: list[str] = []
    if mark.comment:
        lines.append(mark.comment)
    for idx in sorted(int(i) for i in mark.step_marks.keys()):
        sm = mark.step_marks[str(idx)]
        line = f"Step {idx + 1}: {sm['status']}"
        if sm.get("note"):
            line += f" — {sm['note']}"
        if sm.get("overrode"):
            line += f" (overrode agent: {sm['agent_status']})"
        lines.append(line)
    if mark.agent_note:
        if lines:
            lines.append("")
        lines.append(mark.agent_note)
    return "\n".join(lines)


def compose_agent_note(run_case, run_id: str, step_indices: list[int] | None, when: str) -> str:
    """One-paragraph summary of an agent run, written onto the case's mark.

    Step numbers are the ORIGINAL case step numbers (selected indices + 1),
    not tape positions — the tape only contains the selected steps, in order.
    """
    sel = sorted(set(step_indices)) if step_indices else list(range(len(run_case.steps)))
    nums = ", ".join(str(i + 1) for i in sel[: len(run_case.steps)])
    lines = [f"Agent run {when} ({run_id}), steps {nums}: {run_case.status}"]
    for orig, st in zip(sel, run_case.steps):
        evaluation = " ".join((st.evaluation or "").split())
        lines.append(f"Step {orig + 1}: {st.status} — {evaluation}")
    return "\n".join(lines)


class ManualStore:
    """In-memory marks keyed by plan, snapshotted to disk per plan."""

    def __init__(self) -> None:
        # plan_key -> {case_id -> ManualMark}
        self._marks: dict[str, dict[str, ManualMark]] = {}
        # plan_key -> last built ManualSession (so /mark can return updated case)
        self._sessions: dict[str, ManualSession] = {}

    # ---------------------------------------------------------------- build
    def build(
        self,
        plan_key: str,
        plan_name: str,
        raw_cases: list[dict[str, Any]],
        qmetry_configured: bool,
    ) -> ManualSession:
        marks = self._load_marks(plan_key)
        cases: list[ManualCase] = []
        for rc in raw_cases:
            cid = rc["id"]
            cases.append(
                ManualCase(
                    id=cid,
                    name=rc.get("name", cid),
                    steps=rc.get("steps", []),
                    mark=marks.get(cid, ManualMark()),
                    execution_id=rc.get("_qmetry_execution_id"),
                    execution_cycle_id=rc.get("_qmetry_cycle_id"),
                    tc_id=rc.get("_qmetry_tc_id"),
                    version_no=rc.get("_qmetry_version_no", 1),
                    precondition=rc.get("precondition", ""),
                )
            )
        session = ManualSession(
            plan=Plan(key=plan_key, name=plan_name or plan_key),
            qmetry_configured=qmetry_configured,
            cases=cases,
        )
        self._sessions[plan_key] = session
        return session

    def get(self, plan_key: str) -> ManualSession | None:
        return self._sessions.get(plan_key)

    # ---------------------------------------------------------------- mutate
    def set_mark(
        self,
        plan_key: str,
        case_id: str,
        status: str,
        comment: str,
        failed_steps: list[int],
    ) -> ManualCase:
        case = self._require_case(plan_key, case_id)
        case.mark.status = status
        case.mark.comment = comment
        case.mark.failed_steps = list(failed_steps)
        self._persist(plan_key, case_id, case.mark)
        return case

    def set_step_mark(
        self,
        plan_key: str,
        case_id: str,
        step_index: int,
        status: str,
        note: str = "",
        agent_status: AgentStatus = None,
    ) -> ManualCase:
        """Record a hand mark on a single step (pass/fail/blocked/skip).

        Recomputes the case's derived status and back-compat failed_steps list,
        then persists.
        """
        if status not in ("pass", "fail", "blocked", "skip"):
            raise ValueError(f"invalid step mark status: {status!r}")
        case = self._require_case(plan_key, case_id)
        overrode = bool(agent_status) and status != agent_status
        case.mark.step_marks[str(step_index)] = {
            "status": status,
            "note": note,
            "agent_status": agent_status,
            "overrode": overrode,
        }
        case.mark.status = derive_case_status(case.mark.step_marks)
        case.mark.failed_steps = sorted(
            int(i) for i, sm in case.mark.step_marks.items() if sm["status"] == "fail"
        )
        self._persist(plan_key, case_id, case.mark)
        return case

    def set_credentials(
        self, plan_key: str, case_id: str, username: str, password: str
    ) -> ManualCase:
        """Per-case login for the agent. Both empty clears back to the .env
        default; a username with an empty password keeps the stored password
        (so fixing a typo'd username doesn't force retyping the secret)."""
        case = self._require_case(plan_key, case_id)
        if not username and not password:
            case.mark.login_username = ""
            case.mark.login_password = ""
        else:
            case.mark.login_username = username
            if password:
                case.mark.login_password = password
        self._persist(plan_key, case_id, case.mark)
        return case

    def set_agent(
        self,
        plan_key: str,
        case_id: str,
        agent_status: AgentStatus,
        agent_run_id: str | None,
        agent_steps: list[int] | None = _UNSET,
        agent_note: Any = _UNSET,
    ) -> None:
        case = self._require_case(plan_key, case_id)
        case.mark.agent_status = agent_status
        case.mark.agent_run_id = agent_run_id
        if agent_steps is not _UNSET:
            case.mark.agent_steps = list(agent_steps) if agent_steps is not None else None
        if agent_note is not _UNSET:
            case.mark.agent_note = agent_note or ""
        self._persist(plan_key, case_id, case.mark)

    def mark_pushed(self, plan_key: str, case_id: str) -> None:
        case = self._require_case(plan_key, case_id)
        case.mark.pushed_to_qmetry = True
        self._persist(plan_key, case_id, case.mark)

    # ---------------------------------------------------------------- internals
    def _require_case(self, plan_key: str, case_id: str) -> ManualCase:
        session = self._sessions.get(plan_key)
        if session is None:
            raise KeyError(f"No manual session for plan {plan_key!r}; GET it first")
        return session.find_case(case_id)

    def _marks_path(self, plan_key: str) -> Path:
        safe = plan_key.replace("/", "_")
        return MANUAL_DIR / f"{safe}.json"

    def _load_marks(self, plan_key: str) -> dict[str, ManualMark]:
        if plan_key in self._marks:
            return self._marks[plan_key]
        path = self._marks_path(plan_key)
        marks: dict[str, ManualMark] = {}
        if path.exists():
            # utf-8-sig: tolerate a BOM from hand edits (PowerShell's
            # Set-Content -Encoding utf8 writes one; plain utf-8 chokes).
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
            marks = {cid: ManualMark.from_dict(m) for cid, m in raw.items()}
        self._marks[plan_key] = marks
        return marks

    def _persist(self, plan_key: str, case_id: str, mark: ManualMark) -> None:
        # include_secrets=True: the snapshot keeps login_password in PLAINTEXT —
        # manual_sessions/ carries the same trust level as .env (local machine
        # only, gitignored). Browser payloads never use include_secrets.
        marks = self._marks.setdefault(plan_key, {})
        marks[case_id] = mark
        MANUAL_DIR.mkdir(parents=True, exist_ok=True)
        path = self._marks_path(plan_key)
        path.write_text(
            json.dumps(
                {cid: m.to_dict(include_secrets=True) for cid, m in marks.items()},
                indent=2,
            ),
            encoding="utf-8",
        )
