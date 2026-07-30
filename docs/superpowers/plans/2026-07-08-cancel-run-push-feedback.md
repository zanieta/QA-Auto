# Cancel Run + Push Feedback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cancelable agent runs, a push button with real feedback that nothing overlaps, and a Manual-tab polish pass (spec: `docs/superpowers/specs/2026-07-08-cancel-run-push-feedback-design.md` — the spec is the brief; it names every file, behavior, test, and acceptance check).

**Architecture:** Backend: one cancel endpoint + a CancelledError branch in `_run_agent_case`. Frontend: cancel button + busy states + remove the floating duplicate toast + CSS polish.

**Tech Stack:** Python 3.14 / FastAPI; React + Vite.

## Global Constraints

- `.venv\Scripts\python.exe` from C:\Users\rsantos\AI\QA; NOT a git repo; tests never hit network/Chromium.
- No credential leaks; no run_state shape change; FRONTEND.md updated with the UI changes in the same task.
- `prefers-reduced-motion` must disable the new spinner/transitions.

---

### Task 1: backend cancel (server.py + tests) — TDD per spec §1/§Tests

**Files:** Modify `server.py` (`_run_agent_case` ~line 191; new endpoint near the runs endpoints); Test `tests/test_server.py`.
**Interfaces produced:** `POST /runs/{run_id}/cancel` → `{"cancelled": true}` | 404; cancelled runs: mark `agent_status=None`, note "…cancelled by tester", CancelledError re-raised.

### Task 2: frontend cancel + push feedback + polish — spec §1-§4

**Files:** Modify `frontend/src/components/ManualCase.jsx`, `frontend/src/components/ManualView.jsx`, `frontend/src/hooks/useManualState.js` (add `cancelRun`), `frontend/src/tokens.css`, `FRONTEND.md`.
**Gate:** `npm run build` green + full pytest green.

### Task 3: verification (controller-run) — spec §Acceptance 1-4 live.
