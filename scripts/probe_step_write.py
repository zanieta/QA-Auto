"""Discover the correct QMetry test-step-execution result WRITE contract.

Our PUT to .../teststeps/{testStepExecutionId} with {"executionResultId": ...}
returns 200 but does NOT change the step result (steps keep showing the
case-level status). Try candidate (method, path, body) shapes on ONE step and
read back after each; stop at the first shape that actually flips the step.

Target: TC-2211 step[0] -> Pass (our run judged it pass; QMetry shows Fail).
Read-back is the oracle. Run:
  .venv\\Scripts\\python.exe scripts\\probe_step_write.py
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()
try:
    import truststore

    truststore.inject_into_ssl()
except Exception:
    pass

from agent.qmetry import QMetryClient  # noqa: E402

CASE_KEY = "SOUSCLOUD-TC-2211"
PASS_ID = 101543  # from get_execution_results


async def read_step0(client, cid, exec_id):
    ex = (await client._request(
        "GET", f"/testcycles/{cid}/testcase-executions/{exec_id}/teststeps"))["data"]
    row = ex[0]
    name = (row.get("executionResult") or {}).get("name")
    return row, name


async def main():
    client = QMetryClient()
    cid = (await client.get_test_cycle("1ZwYH2ObF7AGZa"))["id"]
    rows = await client.search_test_cases(cid)
    r = next(x for x in rows if x.get("key") == CASE_KEY)
    exec_id = r["testCaseExecutionId"]

    row0, before = await read_step0(client, cid, exec_id)
    tse = row0.get("testStepExecutionId")
    tsid = row0.get("testStepId")
    parm = row0.get("executionParameterRowMapId")
    print(f"exec_id={exec_id} testStepExecutionId={tse} testStepId={tsid} "
          f"execParamRowMapId={parm} | step0 result BEFORE = {before}")

    base = f"/testcycles/{cid}/testcase-executions/{exec_id}"
    step = {"testStepExecutionId": tse, "executionResultId": PASS_ID}
    candidates = [
        # Strong hypothesis: the case-execution PUT accepts a per-step array.
        ("PUT", base, {"testStepExecutions": [step]}),
        ("PUT", base, {"teststeps": [step]}),
        ("PUT", base, {"testStepExecutionResults": [step]}),
        # Collection POST variants.
        ("POST", f"{base}/teststeps", {"testStepExecutions": [step]}),
        ("POST", f"{base}/teststeps", [step]),
        ("POST", f"{base}/teststeps", {"data": [step]}),
        # testStepId in the path instead of the execution id.
        ("PUT", f"{base}/teststeps/{tsid}", {"executionResultId": PASS_ID}),
        # PATCH the step-execution item.
        ("PATCH", f"{base}/teststeps/{tse}", {"executionResultId": PASS_ID}),
    ]

    for i, (method, path, body) in enumerate(candidates):
        try:
            resp = await client._request(method, path, json=body)
            code = "2xx"
        except Exception as e:
            print(f"[{i}] {method} {path}  body={_short(body)} -> ERR {str(e)[:120]}")
            continue
        _row, after = await read_step0(client, cid, exec_id)
        flipped = "  <<< FLIPPED!" if after == "Pass" else ""
        print(f"[{i}] {method} {path}  body={_short(body)} -> {code}; step0 now = {after}{flipped}")
        if after == "Pass":
            print(f"\nWINNER: {method} {path}\n  body shape: {_short(body)}")
            return
    print("\nno candidate flipped step0 to Pass")


def _short(b):
    s = str(b)
    return s if len(s) <= 90 else s[:90] + "..."


if __name__ == "__main__":
    asyncio.run(main())
