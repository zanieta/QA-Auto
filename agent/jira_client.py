"""Jira REST API v3 client.

Auth: Basic — base64(`email:api_token`).
Only called when AUTO_CREATE_BUGS=true OR the frontend "Log failures to Jira"
button fires `POST /runs/{id}/log-bugs`.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import random
from typing import Any

import httpx

from agent.run_state import RunState, TestCase

log = logging.getLogger(__name__)


class JiraError(Exception):
    """Raised on a Jira API failure after retries."""


class JiraClient:
    def __init__(
        self,
        base_url: str | None = None,
        email: str | None = None,
        api_token: str | None = None,
        project_key: str | None = None,
        issue_type: str | None = None,
        timeout: float = 30.0,
        max_attempts: int = 3,
        http_client: httpx.AsyncClient | None = None,
    ):
        self.base_url = (base_url or os.environ["JIRA_BASE_URL"]).rstrip("/")
        self.email = email or os.environ["JIRA_EMAIL"]
        self.api_token = api_token or os.environ["JIRA_API_TOKEN"]
        self.project_key = project_key or os.environ["JIRA_PROJECT_KEY"]
        self.issue_type = issue_type or os.environ.get("JIRA_BUG_ISSUE_TYPE", "Bug")
        self.timeout = timeout
        self.max_attempts = max_attempts
        self._client = http_client
        self._owns_client = http_client is None

    @property
    def _basic_token(self) -> str:
        return base64.b64encode(f"{self.email}:{self.api_token}".encode()).decode()

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Basic {self._basic_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------ public

    async def create_bug(
        self,
        summary: str,
        description: str,
        labels: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a bug issue. Returns the parsed response (`{"id","key","self"}`)."""
        body = {
            "fields": {
                "project": {"key": self.project_key},
                "issuetype": {"name": self.issue_type},
                "summary": summary,
                "description": _adf_paragraph(description),
                "labels": labels or [],
            }
        }
        return await self._request("POST", "/rest/api/3/issue", json=body)

    async def add_comment(self, issue_key: str, body: str) -> dict[str, Any]:
        """Append a comment to an issue."""
        return await self._request(
            "POST",
            f"/rest/api/3/issue/{issue_key}/comment",
            json={"body": _adf_paragraph(body)},
        )

    async def attach_file(
        self,
        issue_key: str,
        filename: str,
        content: bytes,
        mime: str = "image/png",
    ) -> dict[str, Any]:
        """Attach a binary to an issue. Uses multipart + X-Atlassian-Token."""
        url = f"{self.base_url}/rest/api/3/issue/{issue_key}/attachments"
        headers = {
            "Authorization": f"Basic {self._basic_token}",
            "X-Atlassian-Token": "no-check",
        }
        files = {"file": (filename, content, mime)}
        return await self._send_multipart(url, headers, files)

    # ----------------------------------------------------------------- private

    async def _request(self, method: str, path: str, *, json=None) -> dict[str, Any]:
        url = self.base_url + path
        last: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                client = self._get_client()
                if method == "POST":
                    r = await client.post(url, headers=self._headers, json=json)
                elif method == "GET":
                    r = await client.get(url, headers=self._headers)
                else:
                    raise JiraError(f"Unsupported method {method}")
            except (httpx.TransportError, httpx.TimeoutException) as e:
                last = e
                log.warning("Jira transport error (attempt %d/%d): %s",
                            attempt, self.max_attempts, e)
                await self._backoff(attempt)
                continue
            if r.status_code == 429 or 500 <= r.status_code < 600:
                last = JiraError(f"Jira HTTP {r.status_code}: {r.text[:200]}")
                log.warning("Jira %d (attempt %d/%d), retrying",
                            r.status_code, attempt, self.max_attempts)
                await self._backoff(attempt, r.headers.get("retry-after"))
                continue
            if r.status_code >= 400:
                raise JiraError(f"Jira HTTP {r.status_code}: {r.text[:500]}")
            return r.json() if r.content else {}
        raise JiraError(f"Jira call failed after {self.max_attempts} attempts: {last}")

    async def _send_multipart(self, url, headers, files):
        last: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                client = self._get_client()
                r = await client.post(url, headers=headers, files=files)
            except (httpx.TransportError, httpx.TimeoutException) as e:
                last = e
                await self._backoff(attempt)
                continue
            if r.status_code == 429 or 500 <= r.status_code < 600:
                last = JiraError(f"Jira HTTP {r.status_code}: {r.text[:200]}")
                await self._backoff(attempt, r.headers.get("retry-after"))
                continue
            if r.status_code >= 400:
                raise JiraError(f"Jira HTTP {r.status_code}: {r.text[:500]}")
            return r.json() if r.content else {}
        raise JiraError(f"Jira attach failed after {self.max_attempts} attempts: {last}")

    async def _backoff(self, attempt: int, retry_after: str | None = None) -> None:
        if retry_after:
            try:
                await asyncio.sleep(float(retry_after))
                return
            except ValueError:
                pass
        delay = (2 ** (attempt - 1)) + random.uniform(0, 0.5)
        await asyncio.sleep(delay)


# ---------------------------------------------------------------- helpers


def _adf_paragraph(text: str) -> dict[str, Any]:
    """Tiny ADF (Atlassian Document Format) wrapper for plain text.

    Jira REST v3 requires ADF for description / comment bodies. The minimum
    valid document is a single paragraph with one text node.
    """
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": text}],
            }
        ],
    }


def bugs_from_failed_run(state: RunState) -> list[dict[str, str]]:
    """Compose one bug payload per FAILED case in `state`.

    Returns a list of `{"summary", "description"}` ready to pass to `create_bug`.
    Cases that PASSED / are BLOCKED / are QUEUED are skipped.
    """
    out: list[dict[str, str]] = []
    for case in state.test_cases:
        if case.status != "fail":
            continue
        out.append({
            "summary": f"[QA Agent] {case.id} — {case.name}",
            "description": _format_bug_description(state, case),
        })
    return out


def _format_bug_description(state: RunState, case: TestCase) -> str:
    failing = next((s for s in case.steps if s.status == "fail"), None)
    lines = [
        f"QA Agent detected a failure during run {state.run_id}.",
        "",
        f"Plan: {state.plan.key} — {state.plan.name}",
        f"Test case: {case.id} — {case.name}",
        "",
    ]
    if failing:
        lines += [
            "Failing step:",
            f"  Action: {failing.action}",
        ]
        if failing.test_data:
            lines.append(f"  Test data: {failing.test_data}")
        lines += [
            f"  Detail: {failing.detail or '—'}",
            f"  Evaluation: {failing.evaluation or '—'}",
            f"  Duration: {failing.duration_seconds or 0:.2f}s",
            "",
        ]
    lines += [
        f"Run summary: {state.summary['passed']} passed, {state.summary['failed']} failed, "
        f"{state.summary['blocked']} blocked of {state.summary['total']}.",
    ]
    return "\n".join(lines)
