"""Global console settings — persisted to `settings.json` at the repo root.

Separate from `ManualStore` (per-case tester marks) and `.env` (deploy-time
config): this is the one small bit of server-side state a tester changes from
the console itself and expects to survive a restart.

Currently holds one value: the global **target URL** override — which server
the agent runs against for every run, full-plan or single-case Manual alike.
Test cases sometimes name the server explicitly in their own step text (e.g.
"open https://test.souscheftech.com/login or https://souscheftech.com/login"),
and a tester needs to point the whole console at Test or Production without
editing `.env` and restarting. Empty/unset falls back to `APP_BASE_URL`,
exactly as before this setting existed.

Follows the same survive-a-restart pattern `ManualStore` uses for
`manual_sessions/<plan>.json`, just flatter — one file, no per-plan/per-case
keying, since the value is global.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SETTINGS_PATH = Path(__file__).resolve().parent.parent / "settings.json"

_DEFAULTS: dict[str, Any] = {"target_url": ""}


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
