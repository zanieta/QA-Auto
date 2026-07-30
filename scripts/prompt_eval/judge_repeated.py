"""Judge captured evaluator inputs N times with the CURRENT prompt file.

The evaluator prompt (prompts/result_evaluator.txt) loads fresh from disk on
every call, so the loop is: edit the prompt -> rerun this -> read the verdict
distribution. Validate BOTH directions before shipping a prompt change:
the captured case must converge on the intended verdict, and a contrasting
payload (e.g. the same frames with an unconditional step_text) must keep its
old verdict — a rule that flips everything to "pass" is worse than none.

2026-07-08 result for the record: the CONDITIONAL TRIAGE rule alone went
0/5 -> 1/5 pass; adding a mandatory `waived` FIELD to the output JSON went
5/5. For gpt-4o compliance, output-schema slots beat rule bullets.

Usage (from the repo root):
  .venv\\Scripts\\python.exe scripts\\prompt_eval\\judge_repeated.py <payload.json> [N]
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from dotenv import load_dotenv

load_dotenv(REPO / ".env")

from agent.azure_ai import AzureAIClient


async def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 5

    client = AzureAIClient()
    counts: dict[str, int] = {}
    for i in range(n):
        r = await client.evaluate_result(
            payload["frames"],
            payload["expected"],
            performed=payload.get("performed", ""),
            step_text=payload.get("step_text", ""),
        )
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        reason = " ".join(r["reason"].split())[:160]
        print(f"{i + 1}. {r['status'].upper():7} | {reason}")
    print("---", counts)


if __name__ == "__main__":
    asyncio.run(main())
