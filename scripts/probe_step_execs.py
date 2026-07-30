"""READ-ONLY probe: capture QMetry test-step-execution endpoint shapes.

Discovers, for a real test-case execution in a cycle:
  - which endpoint returns the step-execution rows,
  - the per-row id field and result slot,
  - whether row order matches our flattened step order.

Performs NO writes (no create, no PUT). Run:
  .venv\\Scripts\\python.exe scripts\\probe_step_execs.py [cycleIdOrKey]
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

# Make `import agent.*` work when run as a script from the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

try:
    import truststore

    truststore.inject_into_ssl()
except Exception:
    pass

from agent.qmetry import QMetryClient  # noqa: E402

CYCLE = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("QMETRY_DEFAULT_CYCLE", "1ZwYH2ObF7AGZa")


OUT = []  # (label, data-or-error) captured for the UTF-8 file


def _dump(label, data):
    OUT.append((label, data))
    print(f"[captured] {label}")


async def _try(client, method, path, body=None):
    try:
        data = await client._request(method, path, json=body)
        _dump(f"OK {method} {path}", data)
        return data
    except Exception as e:  # noqa: BLE001 — probe prints and continues
        msg = f"{type(e).__name__}: {str(e)[:300]}"
        OUT.append((f"ERR {method} {path}", msg))
        print(f"[404/err] {method} {path}")
        return None


async def main():
    client = QMetryClient()

    cycle = await client.get_test_cycle(CYCLE)
    cycle_id = cycle["id"]
    print(f"cycle {CYCLE} -> internal id {cycle_id}")

    rows = await client.search_test_cases(cycle_id)
    if not rows:
        print("no test cases in cycle — cannot probe step executions")
        return
    # Pick the case with the most steps so ordering is actually testable.
    row = rows[0]
    tc_id = row.get("id")
    exec_id = row.get("testCaseExecutionId")
    version_no = row.get("versionNo", 1)
    print(f"probe target: tc_id={tc_id} exec_id={exec_id} version={version_no} key={row.get('key')}")

    # Our flattened steps (source of truth for ordering comparison).
    steps = await client.get_test_steps(tc_id, version_no)
    print(f"\nflattened test-step count (from get_test_steps): {len(steps)}")
    for i, s in enumerate(steps):
        det = (s.get("stepDetails") or "").strip().replace("\n", " ")[:60]
        print(f"  step[{i}] seqNo={s.get('seqNo')!r} id={s.get('id')!r} :: {det}")

    OUT.append(("flattened get_test_steps", steps))

    # Candidate step-execution endpoints (GET + POST-search variants).
    candidates = [
        ("GET", f"/testcycles/{cycle_id}/testcase-executions/{exec_id}/teststeps", None),
        ("POST", f"/testcycles/{cycle_id}/testcase-executions/{exec_id}/teststeps/search", {}),
        ("GET", f"/testcycles/{cycle_id}/testcase-executions/{exec_id}/teststep-executions", None),
        ("POST", f"/testcycles/{cycle_id}/testcase-executions/{exec_id}/teststeps/search",
         {"filter": {}}),
    ]
    for method, path, body in candidates:
        await _try(client, method, path, body)

    out_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "probe_step_execs_out.json"
    )
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            [{"label": lbl, "data": data} for lbl, data in OUT],
            f, indent=2, ensure_ascii=False, default=str,
        )
    print(f"\nwrote {os.path.abspath(out_path)}")


if __name__ == "__main__":
    asyncio.run(main())
