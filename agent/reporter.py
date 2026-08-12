"""HTML summary report generator.

After a run, writes `reports/run_<timestamp>.html`:
  - run totals (pass / fail / blocked / elapsed)
  - per-case table with status, reason, per-step timings

Self-contained HTML — inline CSS using the Duke navy palette. No external assets,
so the file works when opened directly from the filesystem.
v1 skips screenshot thumbnails; the run_state model doesn't carry screenshots
yet. Add later by storing PNG paths on each Step and rendering as <img>.
"""

from __future__ import annotations

import html
import logging
from datetime import datetime
from pathlib import Path

from agent.run_state import RunState, TestCase

log = logging.getLogger(__name__)

REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"

_STYLE = """
:root {
  --navy: #1B2A6B;
  --navy-soft: #EEF1FA;
  --navy-line: #DDE3F2;
  --paper: #F7F8FC;
  --white: #FFFFFF;
  --ink: #1A1D2E;
  --muted: #6A7290;
  --faint: #9AA0B8;
  --line: #E7EAF3;
  --green: #1F9D6B; --green-soft: #E6F5EE;
  --red:   #D8453E; --red-soft:   #FBEAE9;
  --amber: #C9881A; --amber-soft: #FBF1DC;
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; background: var(--paper); color: var(--ink); font-family: 'Inter', system-ui, sans-serif; font-size: 14px; }
.wrap { max-width: 1080px; margin: 0 auto; padding: 32px 24px 64px; }
h1 { font-size: 22px; margin: 0 0 4px; font-weight: 600; }
.plan-key { font-family: 'DM Mono', ui-monospace, monospace; color: var(--navy); background: var(--navy-soft); padding: 3px 8px; border-radius: 4px; font-size: 12px; }
.meta { color: var(--muted); font-size: 12px; margin-top: 8px; }
.stats { display: flex; gap: 32px; margin: 24px 0 28px; padding: 18px 24px; background: var(--white); border: 1px solid var(--line); border-radius: 8px; }
.stat { display: flex; flex-direction: column; }
.stat-label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.07em; color: var(--muted); font-weight: 600; }
.stat-value { font-family: 'DM Mono', monospace; font-size: 22px; margin-top: 4px; }
.stat-value.pass    { color: var(--green); }
.stat-value.fail    { color: var(--red); }
.stat-value.blocked { color: var(--amber); }
.case { background: var(--white); border: 1px solid var(--line); border-radius: 8px; margin-bottom: 14px; overflow: hidden; }
.case-head { display: flex; align-items: center; gap: 12px; padding: 14px 18px; border-bottom: 1px solid var(--line); }
.case-id { font-family: 'DM Mono', monospace; font-size: 12px; color: var(--navy); background: var(--navy-soft); padding: 3px 8px; border-radius: 4px; }
.case-name { font-weight: 600; font-size: 14px; flex: 1; }
.badge { font-size: 11px; font-weight: 600; padding: 3px 9px; border-radius: 999px; text-transform: uppercase; letter-spacing: 0.05em; }
.badge.pass    { background: var(--green-soft); color: var(--green); }
.badge.fail    { background: var(--red-soft);   color: var(--red); }
.badge.blocked { background: var(--amber-soft); color: var(--amber); }
.badge.queued  { background: var(--navy-soft);  color: var(--navy); }
.badge.running { background: var(--navy-soft);  color: var(--navy); }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th { text-align: left; font-weight: 600; font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); padding: 8px 18px; background: var(--paper); border-bottom: 1px solid var(--line); }
td { padding: 10px 18px; border-bottom: 1px solid var(--line); vertical-align: top; }
tr:last-child td { border-bottom: 0; }
td.mono, td.duration { font-family: 'DM Mono', monospace; font-size: 12px; color: var(--muted); }
td.duration { text-align: right; white-space: nowrap; }
.eval { font-style: normal; }
.eval.pass { color: var(--green); }
.eval.fail { color: var(--red); }
.eval.blocked { color: var(--amber); }
.empty { padding: 16px 18px; color: var(--muted); font-size: 13px; }
.footer { color: var(--faint); font-size: 11px; margin-top: 24px; text-align: center; }
"""


def generate_report(state: RunState) -> Path:
    """Render `state` to an HTML file under reports/. Returns the file path."""
    REPORTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = REPORTS_DIR / f"run_{ts}_{state.run_id}.html"
    path.write_text(_render(state), encoding="utf-8")
    log.info("Wrote report %s", path)
    return path


# ---------------------------------------------------------------- internals


def _render(state: RunState) -> str:
    s = state.summary
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>QA Agent Run · {html.escape(state.plan.key)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=DM+Mono&display=swap" rel="stylesheet">
<style>{_STYLE}</style>
</head>
<body>
<div class="wrap">
  <h1>QA Agent run summary</h1>
  <div><span class="plan-key">{html.escape(state.plan.key)}</span> · {html.escape(state.plan.name)}</div>
  <div class="meta">Run ID <code>{html.escape(state.run_id)}</code> · generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>

  <div class="stats">
    <div class="stat"><span class="stat-label">Total</span><span class="stat-value">{s['total']}</span></div>
    <div class="stat"><span class="stat-label">Passed</span><span class="stat-value pass">{s['passed']}</span></div>
    <div class="stat"><span class="stat-label">Failed</span><span class="stat-value fail">{s['failed']}</span></div>
    <div class="stat"><span class="stat-label">Blocked</span><span class="stat-value blocked">{s['blocked']}</span></div>
    <div class="stat"><span class="stat-label">Elapsed</span><span class="stat-value">{state.elapsed_seconds:.1f}s</span></div>
  </div>

  {"".join(_render_case(c) for c in state.test_cases) or '<div class="empty">No cases in this run.</div>'}

  <div class="footer">Duke Manufacturing · QA Agent</div>
</div>
</body>
</html>
"""


def _render_case(case: TestCase) -> str:
    rows = "".join(_render_step_row(s) for s in case.steps) or (
        '<tr><td colspan="4" class="empty">No steps recorded.</td></tr>'
    )
    return f"""
<div class="case">
  <div class="case-head">
    <span class="case-id">{html.escape(case.id)}</span>
    <span class="case-name">{html.escape(case.name)}</span>
    <span class="badge {case.status}">{case.status}</span>
  </div>
  <table>
    <thead><tr><th>Step</th><th>Detail</th><th>Evaluation</th><th style="text-align:right">Duration</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</div>
"""


def _render_step_row(step) -> str:
    duration = (
        f"{step.duration_seconds:.2f}s"
        if step.duration_seconds is not None
        else "—"
    )
    eval_html = html.escape(step.evaluation or "—")
    action_html = html.escape(step.action)
    if step.test_data:
        action_html += (
            f'<br><span class="mono" style="color:#6a7290">'
            f"Test data: {html.escape(step.test_data)}</span>"
        )
    return f"""
<tr>
  <td>{action_html} <span class="badge {step.status}" style="margin-left:6px">{step.status}</span></td>
  <td class="mono">{html.escape(step.detail or "—")}</td>
  <td class="eval {step.status}">{eval_html}</td>
  <td class="duration">{duration}</td>
</tr>"""
