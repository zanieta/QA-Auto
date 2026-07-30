# Step Marks + Override Knowledge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Per-step pass/fail/blocked/skip marks with derived case status; AI-verdict overrides require a note and feed an evaluator knowledge loop (spec: `docs/superpowers/specs/2026-07-09-step-marks-override-knowledge-design.md` — the spec is the brief: each task below implements one numbered spec section, with its tests listed in the spec's Tests section).

**Architecture:** step_marks live on ManualMark (derived case status keeps every downstream consumer — summary, push, QMetry — unchanged); a new `agent/knowledge.py` owns the JSONL override store; the evaluator gains a `guidance` input + mandatory `guidance` output slot; one new step-mark endpoint; the case card swaps its case-level bar for per-step buttons.

**Tech Stack:** Python 3.14 (`.venv\Scripts\python.exe`), pytest (mocked), React + Vite.

## Global Constraints

- NOT a git repo — no git; each task's gate is its named test command green.
- Tests never hit network/Chromium. No credentials anywhere near the knowledge file or prompts.
- run_state contract unchanged; manual-session contract changes land with FRONTEND.md + both fixtures in the frontend task.
- Knowledge lookups must NEVER break an agent run (guidance failure → "").
- Interim red allowed between Task 1 and Task 6: the two fixture-parity tests fail on the new `step_marks` key — Task 6 owns fixtures; every other task's gate excludes them.

---

### Task 1: manual_state — step_marks, derivation, compose_comment (spec §1)
**Files:** `agent/manual_state.py`; tests in `tests/test_manual_state.py`.
**Produces:** `ManualMark.step_marks`, `derive_case_status(step_marks)`, `ManualStore.set_step_mark(plan_key, case_id, step_index, status, note, agent_status) -> ManualCase` (ValueError on bad status), rewritten `compose_comment`.
**Gate:** `pytest tests/test_manual_state.py -q` green except the 2 fixture-parity tests (report them).

### Task 2: agent/knowledge.py (spec §2)
**Files:** create `agent/knowledge.py`; create `tests/test_knowledge.py` (use tmp_path monkeypatching for KNOWLEDGE_PATH).
**Produces:** `record_override(plan, case_id, step_index, step_text, expected, agent_status, human_status, note, when)`, `lookup_guidance(case_id, step_index, step_text) -> str`.
**Gate:** `pytest tests/test_knowledge.py -q` green.

### Task 3: evaluator guidance input + output slot (spec §3)
**Files:** `agent/azure_ai.py`, `prompts/result_evaluator.txt`; tests in `tests/test_azure_ai.py`.
**Produces:** `evaluate_result(..., guidance: str = "")`; prompt: TESTER GUIDANCE input description, `"guidance"` output slot FIRST (before "waived"), authority rule after CONDITIONAL TRIAGE.
**Gate:** `pytest tests/test_azure_ai.py -q` green.

### Task 4: orchestrator guidance threading (spec §4)
**Files:** `agent/orchestrator.py`; tests in `tests/test_orchestrator.py`.
**Produces:** `_execute_step(..., orig_index: int)` param; live path calls `lookup_guidance(case_id, orig_index, action_text)` in try/except and passes `guidance=` to evaluate_result.
**Gate:** `pytest tests/test_orchestrator.py -q` green.

### Task 5: server step-mark endpoint + knowledge recording (spec §5)
**Files:** `server.py`; tests in `tests/test_server.py`.
**Produces:** `POST /manual/{plan}/cases/{case_id}/steps/{step_index}/mark` with StepMarkBody{status, note="", agent_status=None}; 404/422 rules; records override knowledge; returns case dict.
**Gate:** `pytest tests/test_server.py -q` green.

### Task 6: frontend rework + contract + fixtures (spec §6)
**Files:** `frontend/src/components/ManualCase.jsx`, `frontend/src/hooks/useManualState.js`, `frontend/src/tokens.css`, `FRONTEND.md`, both `sample_manual_state.json` fixtures.
**Gate:** `npm run build` green AND full `pytest tests/ -q` green (fixture parity restored).

### Task 7: verification (controller-run) — spec §Acceptance 1-3 live.
