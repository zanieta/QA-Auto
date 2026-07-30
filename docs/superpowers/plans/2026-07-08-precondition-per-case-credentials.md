# Precondition Display + Per-Case Credentials Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show each QMetry case's Precondition in the Manual card and let the tester set per-case login credentials the agent uses instead of the .env admin (spec: `docs/superpowers/specs/2026-07-08-precondition-and-per-case-credentials-design.md`).

**Architecture:** Precondition rides the existing per-case QMetry hydration (`?fields=summary,precondition`) → case dict → `ManualCase.precondition` → case card. Credentials live on `ManualMark` (password persisted via `to_dict(include_secrets=True)`, never in browser payloads), set via a new `POST …/credentials` endpoint, and flow server → `run_single_case(credentials=…)` → `BrowserSession.credentials` → `login()`.

**Tech Stack:** Python 3.14 (`.venv\Scripts\python.exe`), pytest + AsyncMock + mocked httpx, React + Vite.

## Global Constraints

- Always run Python via `.venv\Scripts\python.exe` from C:\Users\rsantos\AI\QA.
- NOT a git repository — no git commands; a task's gate is its green test run.
- Tests never hit the network or launch Chromium.
- SECURITY INVARIANTS: no credential in any HTTP response, run_state, report, QMetry comment, or Jira bug; the AI model never receives credentials; prompts untouched.
- FRONTEND.md + both `sample_manual_state.json` fixtures must be updated with the shape changes in the same task as the frontend (Task 5).
- Login-page readiness selector stays `input[placeholder="Email address"]`.

---

### Task 1: manual_state — `precondition` + credential fields

**Files:**
- Modify: `C:\Users\rsantos\AI\QA\agent\manual_state.py` (ManualMark ~line 33, ManualCase ~line 68, ManualStore.build ~line 153, new set_credentials near set_mark ~line 186, `_persist` ~line 243)
- Test: `C:\Users\rsantos\AI\QA\tests\test_manual_state.py` (append; reuse the `store` fixture + its "TP-45"/"IRHS-R-01" session like the existing tests; NOTE the two fixture-parity tests will fail once shapes change — that is Task 5's job; report it, don't fix fixtures here)

**Interfaces:**
- Produces (Tasks 2-5 rely on exactly these):
  - `ManualMark.login_username: str = ""`, `ManualMark.login_password: str = ""`.
  - `ManualMark.to_dict(include_secrets: bool = False)` — browser payload has `"login_username"` and `"has_password"` (bool), NEVER `"login_password"`; with `include_secrets=True` it ALSO has `"login_password"` (used only by `_persist`).
  - `ManualCase.precondition: str = ""` — in `ManualCase.to_dict()` as `"precondition"`.
  - `ManualStore.build(...)` passes `precondition=rc.get("precondition", "")`.
  - `ManualStore.set_credentials(plan_key, case_id, username: str, password: str) -> ManualCase` — both empty clears both; non-empty username + empty password keeps the stored password; persists.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_manual_state.py`:

```python
def test_precondition_on_case_to_dict(store):
    # rebuild with a precondition on the raw case
    session = store.build(
        "TP-46", "TP-46",
        [{"id": "C-1", "name": "Case", "steps": [], "precondition": "User has valid Recipe Admin credentials."}],
        qmetry_configured=False,
    )
    d = session.find_case("C-1").to_dict()
    assert d["precondition"] == "User has valid Recipe Admin credentials."


def test_credentials_never_in_browser_payload(store):
    store.set_credentials("TP-45", "IRHS-R-01", "qa.user@dukemfg.com", "s3cret")
    d = store.get("TP-45").find_case("IRHS-R-01").mark.to_dict()
    assert d["login_username"] == "qa.user@dukemfg.com"
    assert d["has_password"] is True
    assert "login_password" not in d
    assert "s3cret" not in str(d)


def test_credentials_persist_and_roundtrip(store):
    from agent.manual_state import ManualMark
    store.set_credentials("TP-45", "IRHS-R-01", "u@x.com", "pw")
    persisted = store.get("TP-45").find_case("IRHS-R-01").mark.to_dict(include_secrets=True)
    assert persisted["login_password"] == "pw"
    again = ManualMark.from_dict(persisted)
    assert (again.login_username, again.login_password) == ("u@x.com", "pw")


def test_set_credentials_clear_and_keep_semantics(store):
    store.set_credentials("TP-45", "IRHS-R-01", "u@x.com", "pw")
    # username-only change keeps the password
    store.set_credentials("TP-45", "IRHS-R-01", "new@x.com", "")
    m = store.get("TP-45").find_case("IRHS-R-01").mark
    assert (m.login_username, m.login_password) == ("new@x.com", "pw")
    # both empty clears both
    store.set_credentials("TP-45", "IRHS-R-01", "", "")
    m = store.get("TP-45").find_case("IRHS-R-01").mark
    assert (m.login_username, m.login_password) == ("", "")
```

- [ ] **Step 2: Run to verify failures**

Run: `.venv\Scripts\python.exe -m pytest tests/test_manual_state.py -q -k "precondition or credential"`
Expected: FAIL (no such attribute/method).

- [ ] **Step 3: Implement** in `agent/manual_state.py`:

`ManualMark` — add fields after `agent_note`:

```python
    login_username: str = ""      # per-case login account; "" = use the .env default
    login_password: str = ""      # persisted to disk only — never serialized to the browser
```

Replace `to_dict` with:

```python
    def to_dict(self, include_secrets: bool = False) -> dict:
        d = {
            "status": self.status,
            "comment": self.comment,
            "failed_steps": list(self.failed_steps),
            "agent_status": self.agent_status,
            "agent_run_id": self.agent_run_id,
            "agent_steps": list(self.agent_steps) if self.agent_steps is not None else None,
            "agent_note": self.agent_note,
            "pushed_to_qmetry": self.pushed_to_qmetry,
            "login_username": self.login_username,
            "has_password": bool(self.login_password),
        }
        if include_secrets:
            d["login_password"] = self.login_password
        return d
```

`from_dict`: add `login_username=d.get("login_username", "")` and
`login_password=d.get("login_password", "")`.

`ManualCase`: add field `precondition: str = ""` and `"precondition": self.precondition,`
to its `to_dict()` (place after `"steps"`).

`ManualStore.build`: pass `precondition=rc.get("precondition", "")` in the
`ManualCase(...)` constructor call.

`ManualStore` — new method after `set_mark`:

```python
    def set_credentials(
        self, plan_key: str, case_id: str, username: str, password: str
    ) -> ManualCase:
        """Per-case login for the agent. Both empty clears back to the .env
        default; a username with an empty password keeps the stored password
        (so fixing a typo'd username doesn't force retyping the secret)."""
        case = self._require_case(plan_key, case_id)
        if not username and not password:
            case.mark.login_username = ""
            case.mark.login_password = ""
        else:
            case.mark.login_username = username
            if password:
                case.mark.login_password = password
        self._persist(plan_key, case_id, case.mark)
        return case
```

`_persist`: change the serialization line to keep secrets on disk:

```python
        path.write_text(
            json.dumps(
                {cid: m.to_dict(include_secrets=True) for cid, m in marks.items()},
                indent=2,
            ),
            encoding="utf-8",
        )
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_manual_state.py -q`
Expected: everything passes EXCEPT the two fixture-parity tests
(`test_fixture_matches_built_session_shape`, `test_frontend_fixture_copy_is_identical`)
which now fail on the new keys — expected, Task 5 owns fixtures. State this in
your report.

---

### Task 2: qmetry — fetch + clean the precondition

**Files:**
- Modify: `C:\Users\rsantos\AI\QA\agent\qmetry.py` (`get_test_case_version_detail` ~line 161; `QMetryCaseSource.list_cases._hydrate` ~line 347)
- Test: `C:\Users\rsantos\AI\QA\tests\test_qmetry.py` (append; the file mocks httpx — follow its existing request-assertion pattern)

**Interfaces:**
- Consumes: existing `clean_step_text` (same module).
- Produces: case dicts from `list_cases` carry `"precondition"` (cleaned, `""` when absent). Task 5's server path needs no change beyond Task 1's `build` (server already passes raw cases through).

**API fact (verified live 2026-07-08):** the version-detail endpoint returns
`precondition` ONLY when the query names it: `?fields=summary,precondition`.
`fields=all` does NOT work. Response text is Jira wiki markup.

- [ ] **Step 1: Write the failing tests** (adapt mechanics to the file's
existing mock style; keep assertions):

```python
async def test_version_detail_requests_precondition_field(...):
    # assert the GET to /testcases/{id}/versions/{no} carries
    # params {"fields": "summary,precondition"}


async def test_list_cases_carries_cleaned_precondition(...):
    # version-detail mock returns {"data": {"summary": "Case",
    #   "precondition": "# One\n# Two *bold*"}}
    # -> case dict has precondition == clean_step_text("# One\n# Two *bold*")
    # and a second case whose detail has no precondition -> ""
```

- [ ] **Step 2: Run to verify failures**

Run: `.venv\Scripts\python.exe -m pytest tests/test_qmetry.py -q -k precondition
Expected: FAIL.

- [ ] **Step 3: Implement.**

`get_test_case_version_detail`:

```python
    async def get_test_case_version_detail(
        self, tc_id: str, version_no: int
    ) -> dict[str, Any]:
        """GET /testcases/{id}/versions/{no} — detail incl. ``summary``.

        Real response wraps the detail under ``data``; the case name is
        ``data.summary``. The API omits ``precondition`` unless the query
        names it explicitly (``fields=all`` does NOT work — verified live).
        """
        resp = await self._request(
            "GET",
            f"/testcases/{tc_id}/versions/{version_no}",
            params={"fields": "summary,precondition"},
        )
        if isinstance(resp, dict) and "data" in resp:
            return resp["data"]
        return resp
```

In `_hydrate` (list_cases), where `name` is read from the detail, also
capture the precondition:

```python
            name = tc_key
            precondition = ""
            try:
                detail = await self._client.get_test_case_version_detail(
                    tc_id, version_no
                )
                name = detail.get("summary") or tc_key
                precondition = clean_step_text(detail.get("precondition") or "")
            except QMetryError:
                log.warning("Could not load name for %s", tc_key, exc_info=True)
```

and add `"precondition": precondition,` to the returned case dict (next to
`"name"`).

- [ ] **Step 4: Run to verify pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_qmetry.py -q`
Expected: ALL pass.

---

### Task 3: credentials plumbing — browser, login, orchestrator

**Files:**
- Modify: `C:\Users\rsantos\AI\QA\agent\browser.py` (`__init__` ~line 77), `C:\Users\rsantos\AI\QA\agent\login.py` (`login()` ~line 37), `C:\Users\rsantos\AI\QA\agent\orchestrator.py` (`run_single_case` ~line 93, `_execute_case` ~line 126)
- Test: `C:\Users\rsantos\AI\QA\tests\test_browser.py`, `C:\Users\rsantos\AI\QA\tests\test_orchestrator.py` (append; login() has no dedicated test file — its override test goes in test_browser.py with a mocked page)

**Interfaces:**
- Produces (Task 4 relies on): `Orchestrator.run_single_case(case_id, plan_key="", dry_run=False, step_indices=None, credentials: tuple[str, str] | None = None)`; `BrowserSession.credentials: tuple[str, str] | None` (attribute, default None).
- `run_plan` is UNCHANGED.

- [ ] **Step 1: Write the failing tests:**

In `tests/test_browser.py` (login-override behavior; patch env to prove
precedence):

```python
@pytest.mark.asyncio
async def test_login_uses_session_credentials_over_env(monkeypatch):
    from agent.login import login

    monkeypatch.setenv("APP_BASE_URL", "https://app.example.com")
    monkeypatch.setenv("APP_USERNAME", "env-user@x.com")
    monkeypatch.setenv("APP_PASSWORD", "env-pw")
    s, page = _session_with_fake_page("https://app.example.com")
    page.locator = MagicMock()
    page.wait_for_url = AsyncMock()
    s.credentials = ("case-user@x.com", "case-pw")

    await login(s)

    fill_args = [c.args for c in page.fill.await_args_list]
    assert ('input[placeholder="Email address"]', "case-user@x.com") in fill_args
    assert ('input[type="password"]', "case-pw") in fill_args


@pytest.mark.asyncio
async def test_login_falls_back_to_env_without_session_credentials(monkeypatch):
    from agent.login import login

    monkeypatch.setenv("APP_BASE_URL", "https://app.example.com")
    monkeypatch.setenv("APP_USERNAME", "env-user@x.com")
    monkeypatch.setenv("APP_PASSWORD", "env-pw")
    s, page = _session_with_fake_page("https://app.example.com")
    page.locator = MagicMock()
    page.wait_for_url = AsyncMock()

    await login(s)

    fill_args = [c.args for c in page.fill.await_args_list]
    assert ('input[placeholder="Email address"]', "env-user@x.com") in fill_args
```

In `tests/test_orchestrator.py` (credentials land on the session before
login; use the file's fakes — the browser factory is injectable):

```python
@pytest.mark.asyncio
async def test_run_single_case_sets_browser_credentials(...):
    # run_single_case(..., credentials=("u@x.com", "pw"))
    # assert the fake browser object has .credentials == ("u@x.com", "pw")
    # and that it was set BEFORE login was awaited (e.g. capture inside the
    # patched agent.orchestrator.login fake: assert browser.credentials there)
```

- [ ] **Step 2: Run to verify failures**

Run: `.venv\Scripts\python.exe -m pytest tests/test_browser.py tests/test_orchestrator.py -q -k credential`
Expected: FAIL.

- [ ] **Step 3: Implement.**

`browser.py` `__init__` — add with the other instance attributes:

```python
        # Optional per-run (username, password) override for login(); set by
        # the orchestrator for Manual-tab runs. None = use the .env account.
        self.credentials: tuple[str, str] | None = None
```

`login.py` — replace the env read at the top of `login()`:

```python
    base_url = os.environ.get("APP_BASE_URL", "").rstrip("/")
    override = getattr(browser, "credentials", None)
    if override:
        username, password = override
    else:
        username = os.environ.get("APP_USERNAME", "")
        password = os.environ.get("APP_PASSWORD", "")
```

(The existing "must be set in .env" error now only fires when neither source
supplies credentials — update its message to
`"No login credentials: set APP_USERNAME/APP_PASSWORD in .env or per-case credentials"`.)

`orchestrator.py` — `run_single_case` gains
`credentials: tuple[str, str] | None = None` and passes it to
`_execute_case(state, match, dry_run=dry_run, step_indices=step_indices, credentials=credentials)`.
`_execute_case` gains the same parameter (default None) and, in the
`if not dry_run:` block, sets it right after the factory call:

```python
            browser = self.browser_factory()
            browser.credentials = credentials
```

(`run_plan`'s call site passes nothing — default None keeps it on .env.)

- [ ] **Step 4: Run to verify pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_browser.py tests/test_orchestrator.py -q`
Expected: ALL pass.

---

### Task 4: server — credentials endpoint + run wiring

**Files:**
- Modify: `C:\Users\rsantos\AI\QA\server.py` (new endpoint near `mark_case` ~line 271; `_run_agent_case` ~line 191)
- Test: `C:\Users\rsantos\AI\QA\tests\test_server.py` (append near the mark/run-agent tests, reusing their fixtures)

**Interfaces:**
- Consumes: `ManualStore.set_credentials(...)` (Task 1), `run_single_case(..., credentials=...)` (Task 3).
- Produces: `POST /manual/{plan}/cases/{case_id}/credentials` `{"username": "", "password": ""}` → updated case dict (no password); `_run_agent_case` passes `credentials=(u, p)` when both non-empty on the mark, else `None`.

- [ ] **Step 1: Write the failing tests:**

```python
def test_credentials_endpoint_sets_and_never_echoes(client, tmp_path, monkeypatch):
    # build session for TP-45 like neighboring tests, then:
    r = client.post(
        "/manual/TP-45/cases/A/credentials",
        json={"username": "u@x.com", "password": "pw"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["manual"]["login_username"] == "u@x.com"
    assert body["manual"]["has_password"] is True
    assert "pw" not in r.text and "login_password" not in r.text
    # and GET /manual/TP-45 must not leak it either
    r2 = client.get("/manual/TP-45")
    assert "pw" not in r2.text and "login_password" not in r2.text


def test_credentials_endpoint_unknown_case_404(client, tmp_path, monkeypatch):
    r = client.post(
        "/manual/TP-45/cases/NOPE/credentials",
        json={"username": "u", "password": "p"},
    )
    assert r.status_code == 404


def test_run_agent_case_passes_credentials(client, tmp_path, monkeypatch):
    # set credentials on case A, then drive server_mod._run_agent_case with a
    # FakeOrch capturing kwargs (pattern: test_run_agent_completion_writes_agent_note)
    # assert captured credentials == ("u@x.com", "pw")
    # and with credentials cleared, captured credentials is None
```

- [ ] **Step 2: Run to verify failures**

Run: `.venv\Scripts\python.exe -m pytest tests/test_server.py -q -k credential`
Expected: FAIL (404 route missing / TypeError).

- [ ] **Step 3: Implement.** In `server.py`:

Pydantic body (next to `MarkBody`):

```python
class CredentialsBody(BaseModel):
    username: str = ""
    password: str = ""
```

Endpoint (after `mark_case`):

```python
@app.post("/manual/{plan}/cases/{case_id}/credentials")
async def set_case_credentials(plan: str, case_id: str, body: CredentialsBody) -> dict:
    """Per-case login for the agent. Kept separate from /mark so status and
    note updates never carry credentials. The response and all /manual
    payloads contain the username only — never the password."""
    try:
        case = MANUAL.set_credentials(plan, case_id, body.username, body.password)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return case.to_dict()
```

`_run_agent_case` — before building the orchestrator:

```python
        creds = None
        session = MANUAL.get(plan)
        if session is not None:
            try:
                mark = session.find_case(case_id).mark
                if mark.login_username and mark.login_password:
                    creds = (mark.login_username, mark.login_password)
            except KeyError:
                pass
```

and pass `credentials=creds` to `orch.run_single_case(...)`.

- [ ] **Step 4: Run to verify pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_server.py -q`
Expected: ALL pass.

---

### Task 5: frontend — precondition block + credentials fields + contract/fixtures

**Files:**
- Modify: `C:\Users\rsantos\AI\QA\frontend\src\components\ManualCase.jsx`, `C:\Users\rsantos\AI\QA\frontend\src\tokens.css`, the api-helper module where `runAgentCase` is defined (follow its import in ManualCase.jsx), `C:\Users\rsantos\AI\QA\FRONTEND.md` (~line 256-270), `C:\Users\rsantos\AI\QA\fixtures\sample_manual_state.json`, `C:\Users\rsantos\AI\QA\frontend\public\fixtures\sample_manual_state.json`
- Verify: `npm run build` in `frontend/`; full pytest green (fixture parity restored).

**Interfaces:**
- Consumes: `case.precondition`, `manual.login_username`, `manual.has_password` (Task 1 payload), `POST /manual/{plan}/cases/{id}/credentials` (Task 4).

- [ ] **Step 1: api helper** — add next to `runAgentCase`:

```js
export async function saveCaseCredentials(plan, caseId, username, password) {
  const r = await fetch(
    `/manual/${encodeURIComponent(plan)}/cases/${encodeURIComponent(caseId)}/credentials`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    }
  )
  if (!r.ok) throw new Error(`credentials save failed (${r.status})`)
  return r.json()
}
```

(Match the file's actual fetch/error conventions — read `runAgentCase` first.)

- [ ] **Step 2: ManualCase.jsx** — between `</header>` and the
`manual-agent-hint` paragraph insert:

```jsx
      {testCase.precondition && (
        <div className="manual-precondition">
          <div className="manual-precondition-label">Precondition</div>
          <div className="manual-precondition-text">{testCase.precondition}</div>
        </div>
      )}
```

After the `manual-agent-hint` paragraph insert the credentials row (local
state `loginUser` initialized from `m.login_username`, `loginPw` initialized
to `''`, `credsMsg` for feedback):

```jsx
      <div className="manual-credentials">
        <span className="manual-credentials-label">Login as</span>
        <input
          type="text"
          placeholder="username (default admin)"
          value={loginUser}
          disabled={agentRunning}
          onChange={(e) => setLoginUser(e.target.value)}
        />
        <input
          type="password"
          placeholder={m.has_password ? '••• saved' : 'password'}
          value={loginPw}
          disabled={agentRunning}
          onChange={(e) => setLoginPw(e.target.value)}
        />
        <button
          type="button"
          className="btn btn-ghost"
          disabled={agentRunning}
          onClick={handleSaveCredentials}
        >
          Save
        </button>
        {credsMsg && <span className="manual-credentials-msg">{credsMsg}</span>}
      </div>
```

with the handler:

```jsx
  async function handleSaveCredentials() {
    setCredsMsg(null)
    try {
      await saveCaseCredentials(plan, testCase.id, loginUser, loginPw)
      setLoginPw('')
      setCredsMsg(loginUser || loginPw ? 'saved' : 'cleared — using default admin')
      await onChanged?.()
    } catch (e) {
      setCredsMsg(e.message)
    }
  }
```

Also sync `loginUser` when the mark refreshes (same `useEffect` that resets
`comment` from `m.comment`): `setLoginUser(m.login_username || '')`.

- [ ] **Step 3: tokens.css** — beside the manual styles (reuse the real
tokens: `--navy-soft`, `--muted`, `--mono`, `--radius-sm` — verify names at
the top of the file):

```css
.manual-precondition {
  margin: 10px 0 0;
  padding: 10px 12px;
  background: var(--navy-soft);
  border-radius: var(--radius-sm);
}

.manual-precondition-label {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--muted);
  margin-bottom: 4px;
}

.manual-precondition-text {
  font-size: 13px;
  line-height: 1.5;
  white-space: pre-wrap;
}

.manual-credentials {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 8px 0 0;
  flex-wrap: wrap;
}

.manual-credentials-label {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--muted);
}

.manual-credentials input {
  font: inherit;
  font-size: 13px;
  padding: 6px 8px;
  border: 1px solid rgba(27, 42, 107, 0.18);
  border-radius: var(--radius-sm);
  min-width: 180px;
}

.manual-credentials-msg {
  font-size: 12px;
  color: var(--muted);
}
```

- [ ] **Step 4: FRONTEND.md** — in the manual-session JSON (~line 256-270):
add `"precondition": "",` after the `"steps"` line on the case object, and on
the manual object add after `"agent_note"`:

```
        "login_username": "",         // per-case agent login; "" = default admin
        "has_password": false,        // a per-case password is saved server-side (never sent here)
```

(Also add the `"agent_note": ""` context line if the JSON block ordering
needs adjusting — keep the block matching `ManualCase.to_dict()` exactly.)

- [ ] **Step 5: fixtures** — add the same three keys
(`"precondition": ""` on each case; `"login_username": ""` and
`"has_password": false` on each manual object) to BOTH
`fixtures/sample_manual_state.json` and
`frontend/public/fixtures/sample_manual_state.json` (they must stay
byte-identical).

- [ ] **Step 6: Build + full suite**

Run (in frontend/): `npm run build` → succeeds.
Run: `.venv\Scripts\python.exe -m pytest tests/ -q` → ZERO failures
(fixture-parity tests green again).

---

### Task 6: verification (controller-run)

- Full suite green; restart server; rehydrate `GET /manual/daYoCqgmH49VMx`.
- TC-2 card shows the precondition block between title and steps.
- `GET /manual/...` response contains no `login_password` and no password
  value anywhere.
- Set per-case credentials on TC-2, run agent step 2, confirm the server log
  shows a login and the run proceeds (its verdict is the app's business);
  clear credentials, re-run, confirm .env admin is used again.
