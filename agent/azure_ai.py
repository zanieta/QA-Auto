"""Azure OpenAI GPT-4o wrapper.

Two responsibilities:
  - translate_step:   English step -> list of {action, selector, value}
  - evaluate_result:  screenshot + expected -> {status, reason}

Prompts load from /prompts/ — never inline them. Vision input goes as a base64
`image_url` content block. Retries up to 3 times on transient errors
(429 / network / 5xx) with exponential backoff.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

VALID_ACTIONS = {
    "navigate",
    "click",
    "fill",
    "select",
    "wait",
    "assert_text",
    "assert_visible",
    "login",
    "logout",
}


class AzureAIError(Exception):
    """Raised when Azure OpenAI returns an unusable response after all retries."""


class AzureAIClient:
    def __init__(
        self,
        endpoint: str | None = None,
        api_key: str | None = None,
        deployment: str | None = None,
        translator_deployment: str | None = None,
        evaluator_deployment: str | None = None,
        evaluator_prompt_file: str | None = None,
        api_version: str | None = None,
        timeout: float = 60.0,
        max_attempts: int = 3,
        http_client: httpx.AsyncClient | None = None,
    ):
        self.endpoint = (endpoint or os.environ["AZURE_AI_ENDPOINT"]).rstrip("/")
        self.api_key = api_key or os.environ["AZURE_AI_API_KEY"]
        self.deployment = deployment or os.environ["AZURE_AI_DEPLOYMENT"]
        # Per-role deployments so the cheap translator and the vision evaluator
        # can run on different models. Both fall back to AZURE_AI_DEPLOYMENT.
        self.translator_deployment = (
            translator_deployment
            or os.environ.get("AZURE_AI_TRANSLATOR_DEPLOYMENT")
            or self.deployment
        )
        self.evaluator_deployment = (
            evaluator_deployment
            or os.environ.get("AZURE_AI_EVALUATOR_DEPLOYMENT")
            or self.deployment
        )
        self.api_version = (
            api_version
            or os.environ.get("AZURE_AI_API_VERSION")
            or "2024-08-01-preview"
        )
        # Which file under /prompts/ the evaluator's system prompt loads from.
        # Defaults to the gpt-4o-tuned prompt so behaviour is unchanged when
        # unset. Override to try a model-specific variant (e.g.
        # result_evaluator_41.txt for gpt-4.1) without touching this file.
        self.evaluator_prompt_file = (
            evaluator_prompt_file
            or os.environ.get("EVALUATOR_PROMPT_FILE")
            or "result_evaluator.txt"
        )
        # Fail loudly here, not on the first evaluate_result call: a missing
        # or unreadable override must crash at construction, never silently
        # fall back to the default prompt (a silently-wrong prompt is worse
        # than a crash for something that judges pass/fail verdicts).
        if not (PROMPTS_DIR / self.evaluator_prompt_file).is_file():
            raise AzureAIError(
                f"EVALUATOR_PROMPT_FILE {self.evaluator_prompt_file!r} not found "
                f"under {PROMPTS_DIR}"
            )
        self.timeout = timeout
        self.max_attempts = max_attempts
        self._client = http_client
        self._owns_client = http_client is None
        # Deployments that rejected `temperature` (reasoning models like the
        # gpt-5.x family only accept the default). Learned at runtime from the
        # first 400, then remembered so later calls skip the failed attempt.
        self._no_temperature: set[str] = set()

    @property
    def _chat_url(self) -> str:
        return self._chat_url_for(self.deployment)

    def _chat_url_for(self, deployment: str) -> str:
        return (
            f"{self.endpoint}/openai/deployments/{deployment}"
            f"/chat/completions?api-version={self.api_version}"
        )

    @property
    def _headers(self) -> dict[str, str]:
        return {"api-key": self.api_key, "Content-Type": "application/json"}

    # ------------------------------------------------------------------ public

    async def translate_step(
        self,
        step_text: str,
        app_context: str | None = None,
        elements: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Convert one English test step into ordered Playwright actions.

        When `elements` is provided (the page snapshot), they are listed in the
        prompt and the model must choose targets by `ref`.
        """
        system = self._load_prompt("step_translator.txt")
        user_parts = [f"STEP: {step_text}"]
        if app_context:
            user_parts.append(f"CONTEXT: {app_context}")
        if elements:
            lines = ["PAGE ELEMENTS (choose by ref; only use refs that exist):"]
            for el in elements:
                kind = el.get("role") or el.get("tag") or "?"
                lines.append(f'  {el.get("ref")}  {kind}  "{el.get("name", "")}"')
            user_parts.append("\n".join(lines))
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": "\n".join(user_parts)},
        ]
        raw = await self._chat(
            messages,
            response_format={"type": "json_object"},
            deployment=self.translator_deployment,
        )
        actions, done = _parse_actions(raw)
        if not actions and not done:
            raise AzureAIError(f"Translator returned no actions for: {step_text!r}")
        # An explicit done with no actions means "the step's goal is already
        # met" — the act→observe loop treats an empty list as the stop signal.
        return actions

    async def evaluate_result(
        self,
        screenshot_b64: str | list[str],
        expected: str,
        performed: str = "",
        step_text: str = "",
        guidance: str = "",
    ) -> dict[str, str]:
        """Decide whether the screenshot(s) satisfy the expected result.

        Accepts a single base64 PNG or an ordered list of frames (one captured
        after each action; the last is the final state). `performed`, when
        non-empty, is the agent's executed-actions summary (see
        `orchestrator._format_detail`) — it lets findings state HOW a step
        with alternatives/optional parts was achieved. `step_text`, when
        non-empty, is the step's instruction — conditional "If available…"
        clauses live THERE, not in the expected text, and the evaluator must
        see them to judge conditionally. `guidance`, when non-empty, is past
        tester overrides of the AI verdict for this exact step (see
        `agent/knowledge.py`) — the tester is the authority on intent. Returns
        `{"status": "pass" | "fail", "reason": "..."}`.
        """
        frames = [screenshot_b64] if isinstance(screenshot_b64, str) else list(screenshot_b64)
        system = self._load_prompt(self.evaluator_prompt_file)
        label = (
            f"EXPECTED: {expected}"
            if len(frames) == 1
            else f"EXPECTED: {expected}\n({len(frames)} screenshots follow, one per "
            "action in execution order; the last shows the final state.)"
        )
        if step_text:
            label = (
                "STEP INSTRUCTION — what the tester asked; conditional clauses "
                f"(\"If available…\", \"If required…\") live here:\n{step_text}\n\n" + label
            )
        if performed:
            label += (
                "\n\nPERFORMED ACTIONS — what the agent actually did, in order:\n"
                f"{performed}"
            )
        if guidance:
            label += (
                "\n\nTESTER GUIDANCE — on past runs of THIS step the tester "
                "overrode the AI verdict (the tester is the authority on "
                f"intent):\n{guidance}"
            )
        content: list[dict[str, Any]] = [{"type": "text", "text": label}]
        for i, frame in enumerate(frames):
            if len(frames) > 1:
                # Label every frame so the model weighs the whole sequence
                # instead of anchoring on the final image.
                content.append(
                    {"type": "text", "text": f"Frame {i + 1} of {len(frames)} (after action {i + 1}):"}
                )
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{frame}"},
                }
            )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ]
        raw = await self._chat(
            messages,
            response_format={"type": "json_object"},
            deployment=self.evaluator_deployment,
        )
        return _parse_evaluation(raw)

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    # ----------------------------------------------------------------- private

    def _load_prompt(self, name: str) -> str:
        return (PROMPTS_DIR / name).read_text(encoding="utf-8")

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def _chat(
        self,
        messages: list[dict[str, Any]],
        response_format: dict | None = None,
        temperature: float = 0.1,
        deployment: str | None = None,
    ) -> str:
        """POST to Azure chat-completions with retry; returns the assistant text."""
        target = deployment or self.deployment
        url = self._chat_url_for(target)
        body: dict[str, Any] = {"messages": messages}
        if target not in self._no_temperature:
            body["temperature"] = temperature
        if response_format is not None:
            body["response_format"] = response_format

        last_exc: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                client = self._get_client()
                resp = await client.post(url, headers=self._headers, json=body)
            except (httpx.TransportError, httpx.TimeoutException) as e:
                last_exc = e
                log.warning("Azure transport error (attempt %d/%d): %s",
                            attempt, self.max_attempts, e)
                await self._backoff(attempt)
                continue

            if resp.status_code == 429 or 500 <= resp.status_code < 600:
                last_exc = AzureAIError(
                    f"Azure HTTP {resp.status_code}: {resp.text[:200]}"
                )
                log.warning("Azure %d (attempt %d/%d), retrying",
                            resp.status_code, attempt, self.max_attempts)
                await self._backoff(attempt, retry_after=resp.headers.get("retry-after"))
                continue

            if resp.status_code == 400 and "temperature" in resp.text and "temperature" in body:
                # Reasoning models (gpt-5.x, o-series) only accept the default
                # temperature. Drop it and retry immediately; remember the
                # deployment so future calls skip the failed attempt.
                self._no_temperature.add(target)
                body = {k: v for k, v in body.items() if k != "temperature"}
                log.info("Deployment %s rejects 'temperature' — retrying without it", target)
                continue

            if resp.status_code >= 400:
                # 4xx other than 429 — not retryable
                raise AzureAIError(
                    f"Azure HTTP {resp.status_code}: {resp.text[:500]}"
                )

            try:
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, ValueError) as e:
                raise AzureAIError(f"Unexpected Azure response shape: {e}") from e

        raise AzureAIError(
            f"Azure call failed after {self.max_attempts} attempts: {last_exc}"
        )

    async def _backoff(self, attempt: int, retry_after: str | None = None) -> None:
        if retry_after:
            try:
                await asyncio.sleep(float(retry_after))
                return
            except ValueError:
                pass
        # exp backoff 1s, 2s, 4s with jitter
        delay = (2 ** (attempt - 1)) + random.uniform(0, 0.5)
        await asyncio.sleep(delay)


# ---------------------------------------------------------------- parse helpers


def _parse_actions(raw: str) -> tuple[list[dict[str, Any]], bool]:
    """Coerce the model output into (list[{action, selector, value}], done).

    response_format=json_object forces the model to return an OBJECT, so it never
    returns a bare array. We accept any of the shapes it actually produces:
      - {"actions": [...]} / {"steps": [...]} / {"result": [...]}  (wrapper key)
      - {"action": "...", ...}                                      (single action)
      - {<anything>: [...]}                                         (one list value)
    `done` is True when the model explicitly signals the step's goal is already
    complete ({"actions": [], "done": true}) — the act→observe loop stops there.
    """
    data = _loads_loosely(raw)
    done = bool(isinstance(data, dict) and data.get("done"))
    if isinstance(data, dict) and done and not data.get("actions"):
        return [], True
    if isinstance(data, dict):
        wrapped = None
        for key in ("actions", "steps", "result", "items", "playwright_actions"):
            if isinstance(data.get(key), list):
                wrapped = data[key]
                break
        if wrapped is not None:
            data = wrapped
        elif "action" in data:
            # a single action object — wrap it
            data = [data]
        else:
            # fall back to the sole list-valued field, if there is exactly one
            list_vals = [v for v in data.values() if isinstance(v, list)]
            if len(list_vals) == 1:
                data = list_vals[0]
    if not isinstance(data, list):
        raise AzureAIError(f"Translator did not return a list, got {type(data).__name__}")
    out: list[dict[str, Any]] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise AzureAIError(f"Action {i} is not an object")
        action = item.get("action")
        if action not in VALID_ACTIONS:
            raise AzureAIError(f"Action {i} has unknown 'action': {action!r}")
        out.append(
            {
                "action": action,
                "ref": item.get("ref"),
                "selector": item.get("selector"),
                "value": item.get("value"),
            }
        )
    return out, done


def _parse_evaluation(raw: str) -> dict[str, str]:
    data = _loads_loosely(raw)
    if not isinstance(data, dict):
        raise AzureAIError(f"Evaluator did not return an object, got {type(data).__name__}")
    status = data.get("status")
    reason = data.get("reason", "")
    if status not in ("pass", "fail", "blocked"):
        raise AzureAIError(f"Evaluator returned bad status: {status!r}")
    if not isinstance(reason, str):
        reason = str(reason)
    findings = data.get("findings")
    if findings and isinstance(findings, str) and "Findings:" not in reason:
        reason = f"{reason}\nFindings: {findings}"
    return {"status": status, "reason": reason}


def _loads_loosely(raw: str) -> Any:
    """Parse JSON, tolerating ```json fences and trailing extra objects.

    Models occasionally emit the same object twice, back to back:
    `{"actions":[…]}\\n{"actions":[…]}`. Strict `json.loads` rejects the whole
    payload ("Extra data: line 2 column 1"), which cost a real run its step
    (SOUSCLOUD-TC-2915 step 3, 2026-08-17). The first complete object is what
    the model meant, so decode that and ignore whatever follows.
    """
    s = raw.strip()
    if s.startswith("```"):
        # strip the first ``` ... ``` block
        s = s.strip("`")
        if s.startswith("json\n") or s.startswith("json\r"):
            s = s[5:]
    try:
        return json.loads(s)
    except json.JSONDecodeError as e:
        # Take the first complete JSON value and drop any trailing junk.
        try:
            value, _end = json.JSONDecoder().raw_decode(s)
        except json.JSONDecodeError:
            raise AzureAIError(
                f"Model output is not valid JSON: {e}; raw={raw[:200]!r}"
            ) from e
        log.warning(
            "Model returned extra data after the first JSON value; using the "
            "first and discarding %d trailing characters", len(s) - _end
        )
        return value
