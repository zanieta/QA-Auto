"""CLI entry point — run a plan headless, no frontend.

Examples:
  python main.py --plan SOUSCLOUD-TP-45
  python main.py --testcase IRHS-R-01 --dry-run
  HEADLESS=false python main.py --testcase IRHS-R-01
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

from agent.orchestrator import Orchestrator
from agent.reporter import generate_report


def _qmetry_configured() -> bool:
    key = os.environ.get("QMETRY_API_KEY", "")
    return bool(key) and not key.startswith("REPLACE_WITH")


def _qmetry_execution_mode() -> str:
    mode = os.environ.get("QMETRY_EXECUTION_MODE", "edit").strip().lower()
    return "create" if mode == "create" else "edit"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="QA Agent CLI")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--plan", help="Test plan key, e.g. SOUSCLOUD-TP-45")
    g.add_argument("--testcase", help="Single test case id")
    p.add_argument("--dry-run", action="store_true", help="Skip Playwright + writes")
    p.add_argument("--no-report", action="store_true", help="Skip HTML report")
    p.add_argument("--push-qmetry", action="store_true",
                   help="After a plan run, write per-step results to QMetry")
    return p.parse_args()


async def _push_to_qmetry(source, state, plan_key: str) -> None:
    """Push the run's per-case/per-step results to QMetry, reusing the same
    case source the run executed against (so ids + execution ids match).
    Never lets a QMetry error crash the CLI after a successful run."""
    from agent.qmetry import QMetryClient, QMetryError, write_case_execution

    mode = _qmetry_execution_mode()
    try:
        src_cases = {c["id"]: c for c in await source.list_cases(plan_key)}
    except QMetryError as e:
        print(f"--push-qmetry: could not load cycle {plan_key!r} from QMetry: {e}")
        return

    client = QMetryClient()
    try:
        for case in state.test_cases:
            src = src_cases.get(case.id)
            if src is None or src.get("_qmetry_execution_id") is None:
                print(f"skip {case.id}: no QMetry execution id")
                continue
            step_results = {
                i: (s.status, s.evaluation)
                for i, s in enumerate(case.steps)
                if s.status in ("pass", "fail", "blocked")
            }
            try:
                r = await write_case_execution(
                    client,
                    cycle_id=src.get("_qmetry_cycle_id") or plan_key,
                    execution_id=src["_qmetry_execution_id"],
                    tc_id=src.get("_qmetry_tc_id") or case.id,
                    version_no=src.get("_qmetry_version_no", 1),
                    case_status=case.status,
                    step_results=step_results,
                    mode=mode,
                )
                print(f"pushed {case.id}: exec {r.exec_id}, {r.steps_written} steps, {len(r.errors)} errors")
            except QMetryError as e:
                print(f"error {case.id}: {e}")
    finally:
        aclose = getattr(client, "aclose", None)
        if aclose is not None:
            try:
                await aclose()
            except Exception:
                pass


async def _run(args: argparse.Namespace) -> int:
    # --push-qmetry needs the run to execute the REAL QMetry cases (so ids +
    # execution ids match); otherwise use the fixture source as before.
    source = None
    push_wanted = bool(getattr(args, "push_qmetry", False)) and not args.dry_run
    use_qmetry = push_wanted and bool(args.plan)
    if use_qmetry:
        if not _qmetry_configured():
            print("--push-qmetry ignored: QMetry is not configured (set QMETRY_API_KEY).")
            use_qmetry = False
        else:
            from agent.qmetry import QMetryCaseSource
            source = QMetryCaseSource()

    orch = Orchestrator(case_source=source)  # None -> Orchestrator defaults to fixtures

    if args.plan:
        state = await orch.run_plan(args.plan)
    else:
        await orch.run_single_case(args.testcase, dry_run=args.dry_run)
        return 0

    if use_qmetry and source is not None:
        await _push_to_qmetry(source, state, args.plan)

    if not args.no_report:
        path = generate_report(state)
        print(f"Report: {path}")
    return 0 if state.summary["failed"] == 0 else 1


def main() -> int:
    load_dotenv()
    # Use the OS trust store so TLS-inspected corporate networks verify (see server.py).
    try:
        import truststore

        truststore.inject_into_ssl()
    except Exception:
        pass
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    args = parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
