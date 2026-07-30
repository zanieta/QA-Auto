# Clean Start Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** No cycle loads until the tester chooses a TR; a clean start panel takes the stage until then (spec: `docs/superpowers/specs/2026-07-08-clean-start-page-design.md` — the spec IS the task brief; it names every file, behavior, and acceptance check).

**Architecture:** Frontend-only. `App.jsx` stops auto-falling-back to the default cycle for the manual fetch; a new presentational `StartPanel.jsx` renders when no cycle is chosen; `Rail.jsx` gets a placeholder option; CSS + FRONTEND.md.

**Tech Stack:** React + Vite; no backend change.

## Global Constraints

- NOT a git repo — no git; gate is `npm run build` green + `.venv\Scripts\python.exe -m pytest tests/ -q` green (should be untouched at 178).
- Follow FRONTEND.md's token system; reuse existing tokens (`--navy-soft`, `--muted`, `--radius-sm`).
- `?cycle=` URLs must behave byte-for-byte as today.
- No browser storage.

---

### Task 1: the whole change (single frontend task)

**Files:**
- Modify: `frontend/src/App.jsx` (manualPlanKey chain ~line 64-67; stage-area render), `frontend/src/components/Rail.jsx` (select ~line 32-49), `frontend/src/tokens.css`, `FRONTEND.md`
- Create: `frontend/src/components/StartPanel.jsx`

- [ ] Steps: read App.jsx fully; implement per spec §Implementation; `npm run build`; full pytest; report which render path shows the panel on the Live tab too.

### Task 2: verification (controller-run)

- [ ] Spec §Acceptance items 1-5 against the running server.
