"""Shared test setup.

The point of this file is one rule: **the suite must not depend on the
developer's `.env`.** Several agent behaviours take their default from an
environment variable with an in-code fallback (`RUN_MODE`,
`STEP_MAX_ATTEMPTS`, `EVAL_MAX_FRAMES`, ...). `agent/*` and `server.py` call
`load_dotenv()` at import time, so without this fixture a value in `.env`
silently becomes the default the tests exercise.

That is not hypothetical: setting `RUN_MODE=stop_on_fail` in `.env` broke
three passing tests that assert the `continue` behaviour (2026-08-27). The
tests were right and the environment leaked in. Clearing these here means a
test exercises the documented in-code default unless it opts in explicitly —
by constructing with the argument, or by `monkeypatch.setenv`, which still
wins because it is applied after this fixture.
"""

from __future__ import annotations

import pytest

# Behaviour flags with an in-code default that tests rely on. Credentials and
# endpoints are deliberately NOT cleared: tests mock the network, and blanking
# them would only trade one surprise for another.
_BEHAVIOUR_ENV_VARS = (
    "RUN_MODE",
    "STEP_MAX_ATTEMPTS",
    "STEP_ATTEMPT_BUDGET_S",
    "EVAL_MAX_FRAMES",
    "AGENT_LAUNCH_DELAY_S",
    "AUTO_CREATE_BUGS",
    "SCREENSHOT_ON_PASS",
    "HEADLESS",
    "EVALUATOR_PROMPT_FILE",
)


@pytest.fixture(autouse=True)
def _hermetic_behaviour_env(monkeypatch):
    """Run every test against the in-code defaults, not the local .env."""
    for name in _BEHAVIOUR_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    yield
