"""Global console settings — persisted to `settings.json` at the repo root.

Separate from `ManualStore` (per-case tester marks) and `.env` (deploy-time
config): this is the one small bit of server-side state a tester changes from
the console itself and expects to survive a restart.

Holds two things:

- The global **target URL** override — which server the agent runs against
  for every run, full-plan or single-case Manual alike. Test cases sometimes
  name the server explicitly in their own step text (e.g. "open
  https://test.souscheftech.com/login or https://souscheftech.com/login"),
  and a tester needs to point the whole console at a different server
  without editing `.env` and restarting. Empty/unset falls back to
  `APP_BASE_URL`, exactly as before this setting existed.
- The global **default login credentials** (`login_username`/
  `login_password`) — the console-wide fallback account, one tier below a
  per-case override (Manual tab, `ManualStore.set_credentials`) and a
  run-level override (`POST /runs` body), one tier above `.env`
  `APP_USERNAME`/`APP_PASSWORD`. `login_password` is persisted to disk in
  PLAINTEXT (`settings.json` is gitignored, same trust level as `.env` and
  `manual_sessions/`) but must NEVER appear in an HTTP response, a log line,
  `run_state`, or a model prompt — `to_dict()` follows the exact pattern
  `ManualMark.to_dict` uses: expose `login_username` and a boolean
  `has_password`, never the password itself. `set_credentials` mirrors
  `ManualStore.set_credentials`'s semantics exactly: both empty clears back
  to the `.env` account; a username with an empty password KEEPS the stored
  password (so fixing a typo'd username doesn't force retyping the secret).

Follows the same survive-a-restart pattern `ManualStore` uses for
`manual_sessions/<plan>.json`, just flatter — one file, no per-plan/per-case
keying, since both values are global.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SETTINGS_PATH = Path(__file__).resolve().parent.parent / "settings.json"

_DEFAULTS: dict[str, Any] = {
    "target_url": "",
    "login_username": "",
    "login_password": "",
}


class SettingsStore:
    """In-memory settings, snapshotted to disk on every `set()`.

    `path` is overridable for tests; production code should use the module
    default (`SETTINGS_PATH`) via the no-arg constructor.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or SETTINGS_PATH
        self._values: dict[str, Any] = dict(_DEFAULTS)
        self._load()

    def get(self, key: str) -> Any:
        return self._values.get(key, _DEFAULTS.get(key))

    def set(self, key: str, value: Any) -> None:
        self._values[key] = value
        self._save()

    def set_credentials(self, username: str, password: str) -> None:
        """Set the global default login. Mirrors
        `ManualStore.set_credentials`'s semantics exactly: both empty clears
        back to the .env account; a username with an empty password KEEPS
        the stored password (so fixing a typo'd username doesn't force
        retyping the secret)."""
        if not username and not password:
            self._values["login_username"] = ""
            self._values["login_password"] = ""
        else:
            self._values["login_username"] = username
            if password:
                self._values["login_password"] = password
        self._save()

    def credentials_dict(self) -> dict[str, Any]:
        """Non-secret view: username + whether a password is stored. Never
        includes the password itself — safe for an HTTP response."""
        return {
            "login_username": self.get("login_username"),
            "has_password": bool(self.get("login_password")),
        }

    # ---------------------------------------------------------------- internals
    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            # utf-8-sig: tolerate a BOM from hand edits, same as ManualStore.
            raw = json.loads(self._path.read_text(encoding="utf-8-sig"))
        except Exception:
            log.warning(
                "Could not read %s — starting with defaults", self._path, exc_info=True
            )
            return
        if isinstance(raw, dict):
            self._values.update(raw)

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._values, indent=2), encoding="utf-8")
