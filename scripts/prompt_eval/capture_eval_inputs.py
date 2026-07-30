"""Capture the evaluator's exact inputs from one live agent step run.

Runs a single case step through the real orchestrator with an AzureAIClient
wrapper that saves evaluate_result's inputs (frames + expected + performed +
step_text) to a JSON file before delegating. That file feeds
judge_repeated.py so evaluator-prompt edits can be validated offline with
repeated judgments instead of noisy one-off live runs.

Usage (from the repo root):
  .venv\\Scripts\\python.exe scripts\\prompt_eval\\capture_eval_inputs.py \\
      <cycle_id> <case_id> <step_index_0based> [out.json]

Example (the TC-2 "If available, toggle" step):
  .venv\\Scripts\\python.exe scripts\\prompt_eval\\capture_eval_inputs.py \\
      1ZwYH2ObF7AGZa SOUSCLOUD-TC-2 3
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
from agent.orchestrator import Orchestrator
from agent.qmetry import QMetryCaseSource


class CaptureAzure(AzureAIClient):
    def __init__(self, out: Path):
        super().__init__()
        self._out = out

    async def evaluate_result(self, screenshot_b64, expected, performed="", step_text="", guidance=""):
        frames = [screenshot_b64] if isinstance(screenshot_b64, str) else list(screenshot_b64)
        self._out.write_text(
            json.dumps(
                {
                    "frames": frames,
                    "expected": expected,
                    "performed": performed,
                    "step_text": step_text,
                    "guidance": guidance,
                }
            ),
            encoding="utf-8",
        )
        print(f"captured {len(frames)} frames, guidance={'yes' if guidance else 'no'} -> {self._out}")
        return await super().evaluate_result(
            screenshot_b64, expected, performed=performed, step_text=step_text, guidance=guidance
        )


async def main() -> None:
    if len(sys.argv) < 4:
        raise SystemExit(__doc__)
    cycle, case_id, step_index = sys.argv[1], sys.argv[2], int(sys.argv[3])
    out = Path(sys.argv[4]) if len(sys.argv) > 4 else Path(__file__).parent / "eval_input.json"

    orch = Orchestrator(
        case_source=QMetryCaseSource(),
        on_update=lambda s: None,
        azure=CaptureAzure(out),
    )
    state = await orch.run_single_case(case_id, plan_key=cycle, step_indices=[step_index])
    step = state.test_cases[0].steps[0]
    print("live verdict:", step.status, "|", " ".join((step.evaluation or "").split())[:200])


if __name__ == "__main__":
    asyncio.run(main())
