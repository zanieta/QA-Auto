"""Compare (deployment x prompt file) combinations on one captured evaluator input.

Sibling to judge_repeated.py rather than an extension of it: judge_repeated.py
is the quick single-combination loop a developer runs interactively while
iterating on ONE prompt file against the CURRENT deployment. This script
answers a different question — "if we moved the evaluator to gpt-4.1 with
result_evaluator_41.txt, how would its verdicts compare to the gpt-4o
baseline on the same captured input?" — across an arbitrary number of
candidate combinations, without editing any code to add one.

For each --combo "<deployment>:<prompt_file>" (repeatable), the captured
payload is judged N times and this reports:
  - the verdict distribution (pass / fail / blocked counts out of N)
  - the FLIP RATE — whether identical inputs produced different verdicts
    across the N runs of that same combination. This is the specific failure
    mode that disqualified a mini-tier model as the evaluator before (see
    scripts/prompt_eval/judge_repeated.py's docstring): a combination that
    flips on its own input can never be trusted, independent of whether its
    modal verdict looks right.
  - disagreement against the nominated --baseline combo (fraction of the
    combo's N runs whose status differs from the baseline's MODAL status)
  - every `reason` string produced, so a human can read WHY a verdict moved

*** THIS SCRIPT MAKES LIVE AZURE CALLS WHEN RUN FOR REAL. ***
Each judgment costs Azure tokens. Do not run it against real deployments
without the spend being authorised — this is a comparison tool for when a
migration candidate is ready to be validated, not something to run casually.

Usage (from the repo root):
  .venv\\Scripts\\python.exe scripts\\prompt_eval\\compare_combinations.py \\
      <payload.json> \\
      --baseline gpt-4o:result_evaluator.txt \\
      --combo gpt-4o:result_evaluator.txt \\
      --combo gpt-4.1:result_evaluator_41.txt \\
      [-n 5]

The --baseline combo does not need to be repeated as a --combo; it is judged
and reported like any other combo automatically.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from dotenv import load_dotenv

load_dotenv(REPO / ".env")

from agent.azure_ai import AzureAIClient  # noqa: E402  (after sys.path insert)


@dataclass
class ComboResult:
    """One (deployment, prompt_file) combination's N judgments of one payload."""

    deployment: str
    prompt_file: str
    statuses: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.deployment} x {self.prompt_file}"

    @property
    def counts(self) -> Counter:
        return Counter(self.statuses)

    @property
    def modal_status(self) -> str | None:
        if not self.statuses:
            return None
        return self.counts.most_common(1)[0][0]

    @property
    def flip_rate(self) -> float:
        """Fraction of runs that did NOT land on the modal status.

        0.0 means every run of this combo agreed with itself (no flipping).
        Any value above 0 means the same input produced different verdicts
        across identical calls — the failure mode this harness exists to
        catch before it reaches a live run.
        """
        if not self.statuses:
            return 0.0
        _, modal_count = self.counts.most_common(1)[0]
        return 1.0 - (modal_count / len(self.statuses))

    def disagreement_with(self, baseline: "ComboResult") -> float:
        """Fraction of this combo's runs whose status != the baseline's modal status."""
        if not self.statuses or baseline.modal_status is None:
            return 0.0
        mismatches = sum(1 for s in self.statuses if s != baseline.modal_status)
        return mismatches / len(self.statuses)


def parse_combo(spec: str) -> tuple[str, str]:
    """Parse "<deployment>:<prompt_file>" into (deployment, prompt_file)."""
    if ":" not in spec:
        raise argparse.ArgumentTypeError(
            f"combo must be '<deployment>:<prompt_file>', got {spec!r}"
        )
    deployment, prompt_file = spec.split(":", 1)
    if not deployment or not prompt_file:
        raise argparse.ArgumentTypeError(
            f"combo must be '<deployment>:<prompt_file>', got {spec!r}"
        )
    return deployment, prompt_file


async def judge_combo(
    payload: dict[str, Any],
    deployment: str,
    prompt_file: str,
    n: int,
) -> ComboResult:
    """Judge `payload` N times with one (deployment, prompt_file) combination.

    A fresh AzureAIClient is built per combo so each one's evaluator_deployment
    and evaluator_prompt_file are pinned independently — the payload's frames,
    expected result, performed actions, and step text are held constant; only
    the model and prompt vary, which is what makes the comparison meaningful.
    """
    client = AzureAIClient(
        evaluator_deployment=deployment,
        evaluator_prompt_file=prompt_file,
    )
    result = ComboResult(deployment=deployment, prompt_file=prompt_file)
    try:
        for _ in range(n):
            r = await client.evaluate_result(
                payload["frames"],
                payload["expected"],
                performed=payload.get("performed", ""),
                step_text=payload.get("step_text", ""),
                guidance=payload.get("guidance", ""),
            )
            result.statuses.append(r["status"])
            result.reasons.append(r["reason"])
    finally:
        await client.aclose()
    return result


def print_report(results: list[ComboResult], baseline_label: str) -> None:
    """Print the per-combination distribution, flip rate, disagreement, and reasons."""
    print()
    print("=" * 78)
    print(f"baseline: {baseline_label}")
    print("=" * 78)
    baseline = next(r for r in results if r.label == baseline_label)
    for r in results:
        n = len(r.statuses)
        counts = r.counts
        dist = ", ".join(f"{status}={counts.get(status, 0)}" for status in ("pass", "fail", "blocked"))
        print(f"\n--- {r.label} ---")
        print(f"  N={n}  distribution: {dist}")
        print(f"  flip rate (disagrees with own mode): {r.flip_rate:.0%}")
        if r.label != baseline.label:
            print(f"  disagreement vs baseline ({baseline.modal_status}): "
                  f"{r.disagreement_with(baseline):.0%}")
        print("  reasons:")
        for i, (status, reason) in enumerate(zip(r.statuses, r.reasons), start=1):
            print(f"    {i}. {status.upper():7} | {' '.join(reason.split())[:160]}")
    print()


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare (deployment x prompt file) combinations on one captured evaluator input.",
    )
    parser.add_argument("payload", type=Path, help="captured eval_input JSON (see capture_eval_inputs.py)")
    parser.add_argument(
        "--baseline",
        required=True,
        type=parse_combo,
        help="'<deployment>:<prompt_file>' nominated as the baseline for disagreement",
    )
    parser.add_argument(
        "--combo",
        dest="combos",
        action="append",
        type=parse_combo,
        default=[],
        help="'<deployment>:<prompt_file>' to compare; repeatable. The baseline is always included.",
    )
    parser.add_argument("-n", type=int, default=5, help="judgments per combination (default 5)")
    args = parser.parse_args()

    payload = json.loads(args.payload.read_text(encoding="utf-8"))

    combos = list(args.combos)
    if args.baseline not in combos:
        combos.insert(0, args.baseline)

    results = []
    for deployment, prompt_file in combos:
        print(f"judging {deployment} x {prompt_file} ({args.n}x)...", file=sys.stderr)
        results.append(await judge_combo(payload, deployment, prompt_file, args.n))

    baseline_label = f"{args.baseline[0]} x {args.baseline[1]}"
    print_report(results, baseline_label)


if __name__ == "__main__":
    asyncio.run(main())
