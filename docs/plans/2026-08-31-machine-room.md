# Machine room implementation plan (phase 1)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development.

**Goal:** Show the true state of the four engines on the page, and sign in to
Cursor from there.

**Architecture:** One new module, `nightshift/backends.py`, holds a check for
each engine. Cheap checks run on every page load and spend nothing. A real
probe runs only on request and records its cost. A second new module,
`nightshift/signin.py`, owns the Cursor sign-in process and its short-lived
link. The page gets the look that the user approved.

**Tech Stack:** Python 3.14, SQLite, FastAPI, pytest. Spec at
`docs/specs/2026-08-31-machine-room-design.md`.

---

## Measured facts

1. `cursor-agent login` needs `NO_OPEN_BROWSER=1` to stay quiet. It prints a
   link, **broken across three lines**, mixed with ANSI escapes. A plain search
   for `https://\S+` returns only `https://cursor.com/loginDeepControl?`, which
   is useless. `COLUMNS=400` does not stop the wrap. The fix is to strip the
   escapes, join the wrapped lines, and only then read the link.
2. `cursor-agent status` answers `Not logged in` today. The subcommands are
   `login`, `logout`, `status|whoami`, `models`, `mcp`.
3. `gemini` holds `"selectedType": "oauth-personal"` in
   `~/.gemini/settings.json`, and `gemini mcp list` answers
   `No MCP servers configured.`
4. `ollama list` names six local models.
5. `claude auth status` answers JSON with `loggedIn` and `subscriptionType`. It
   is not proof that a run works, so the panel labels it a claim.

---

## Task 1: The cheap checks

**Files:** Create `nightshift/backends.py`, `tests/test_backends.py`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_backends.py
from nightshift import backends


def test_every_engine_reports_a_row():
    names = {e.name for e in backends.check_all(runner=_fake({}))}
    assert names == {"claude", "gemini", "cursor", "ollama"}


def _fake(answers):
    """It answers a command with a fixed text, and it never runs anything."""
    def run(args, **kw):
        for key, text in answers.items():
            if key in " ".join(args):
                return text
        return ""
    return run


def test_cursor_without_a_session_offers_sign_in():
    (cursor,) = [e for e in backends.check_all(
        runner=_fake({"cursor-agent status": "Not logged in"}))
        if e.name == "cursor"]
    assert cursor.signed_in is False
    assert cursor.can_sign_in is True


def test_cursor_with_a_session_does_not_offer_sign_in():
    (cursor,) = [e for e in backends.check_all(
        runner=_fake({"cursor-agent status": "Logged in as ale@example.com"}))
        if e.name == "cursor"]
    assert cursor.signed_in is True
    assert cursor.can_sign_in is False


def test_gemini_reads_its_auth_type_and_sees_no_mail():
    (gemini,) = [e for e in backends.check_all(
        runner=_fake({"gemini mcp list": "No MCP servers configured."}))
        if e.name == "gemini"]
    assert gemini.sees_mail is False


def test_claude_sees_the_mail_when_a_gmail_server_is_connected():
    (claude,) = [e for e in backends.check_all(
        runner=_fake({"claude mcp list": "claude.ai Gmail: https://x - Connected"}))
        if e.name == "claude"]
    assert claude.sees_mail is True


def test_only_claude_may_do_the_mail_work():
    engines = {e.name: e for e in backends.check_all(runner=_fake({}))}
    assert engines["claude"].mail_capable is True
    for other in ("gemini", "cursor", "ollama"):
        assert engines[other].mail_capable is False


def test_a_command_that_hangs_leaves_the_state_unknown():
    def hanging(args, **kw):
        raise TimeoutError("the command did not answer")
    (cursor,) = [e for e in backends.check_all(runner=hanging)
                 if e.name == "cursor"]
    assert cursor.signed_in is None  # unknown, never a guess
```

- [ ] **Step 2: Run them and see them fail**

Run: `.venv/bin/pytest tests/test_backends.py -q`
Expected: FAIL with `ImportError: cannot import name 'backends'`.

- [ ] **Step 3: Write the module**

```python
# nightshift/backends.py
"""What each engine can do, measured with cheap commands.

A cheap check spends no quota and takes about a second. It reports what the
machine claims. A real probe, which costs money, lives in probe() and runs only
when the user asks for it.
"""
import dataclasses
import json
import pathlib
import shutil
import subprocess

CHEAP_TIMEOUT = 8


@dataclasses.dataclass(frozen=True)
class Engine:
    name: str
    label: str
    detail: str
    installed: bool
    signed_in: bool | None      # None means unknown, never a guess
    sees_mail: bool
    forced_schema: bool
    mail_capable: bool
    can_sign_in: bool


def _shell(args, runner=None) -> str:
    """Run a cheap command and give its text. An error gives an empty string."""
    if runner is not None:
        return runner(args)
    try:
        out = subprocess.run(args, capture_output=True, text=True,
                             timeout=CHEAP_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TimeoutError(str(exc)) from exc
    return (out.stdout or "") + (out.stderr or "")


def _safe(args, runner=None) -> str | None:
    try:
        return _shell(args, runner)
    except TimeoutError:
        return None


def _claude(runner) -> Engine:
    auth = _safe(["claude", "auth", "status"], runner)
    signed = None
    if auth is not None:
        try:
            signed = bool(json.loads(auth).get("loggedIn"))
        except (json.JSONDecodeError, AttributeError):
            signed = None
    mcp = _safe(["claude", "mcp", "list"], runner) or ""
    return Engine("claude", "Claude", "Max subscription",
                  installed=bool(shutil.which("claude")), signed_in=signed,
                  sees_mail="Gmail" in mcp, forced_schema=True,
                  mail_capable=True, can_sign_in=False)


def _gemini(runner) -> Engine:
    settings = pathlib.Path.home() / ".gemini" / "settings.json"
    signed = None
    if settings.exists():
        try:
            auth = json.loads(settings.read_text())
            kind = auth.get("security", {}).get("auth", {}).get("selectedType")
            signed = kind == "oauth-personal"
        except (json.JSONDecodeError, OSError):
            signed = None
    mcp = _safe(["gemini", "mcp", "list"], runner) or ""
    return Engine("gemini", "Gemini", "Personal Google account",
                  installed=bool(shutil.which("gemini")), signed_in=signed,
                  sees_mail="Gmail" in mcp, forced_schema=False,
                  mail_capable=False, can_sign_in=False)


def _cursor(runner) -> Engine:
    out = _safe(["cursor-agent", "status"], runner)
    signed = None if out is None else "not logged in" not in out.lower()
    return Engine("cursor", "Cursor", "cursor-agent",
                  installed=bool(shutil.which("cursor-agent")),
                  signed_in=signed, sees_mail=False, forced_schema=False,
                  mail_capable=False, can_sign_in=signed is not True)


def _ollama(runner) -> Engine:
    out = _safe(["ollama", "list"], runner)
    models = 0 if out is None else max(0, len(out.strip().splitlines()) - 1)
    return Engine("ollama", "Ollama", f"{models} local models",
                  installed=bool(shutil.which("ollama")),
                  signed_in=None if out is None else True,
                  sees_mail=False, forced_schema=True,
                  mail_capable=False, can_sign_in=False)


def check_all(runner=None) -> list[Engine]:
    return [_claude(runner), _gemini(runner), _cursor(runner), _ollama(runner)]
```

- [ ] **Step 4: Run the tests again**

Run: `.venv/bin/pytest tests/test_backends.py -q`
Expected: `7 passed`.

- [ ] **Step 5: Commit**

```bash
git add nightshift/backends.py tests/test_backends.py
git commit -m "Measure what each engine can do with cheap commands"
```

---

## Task 2: The sign-in link

The parser is the whole task. The CLI breaks the link across three lines and
mixes ANSI escapes into it, so the naive search gives half a link.

**Files:** Create `nightshift/signin.py`, `tests/test_signin.py`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_signin.py
from nightshift import signin

# The real output of `NO_OPEN_BROWSER=1 cursor-agent login` on 2026-08-31.
REAL_OUTPUT = (
    " Starting login process...\n\n"
    "\x1b[2K\x1b[1A\x1b[2K\x1b[1A\x1b[2K\x1b[G Authenticating with Cursor...\n\n"
    " Waiting for browser authentication...\n"
    " Open a browser and navigate to this link: https://cursor.com/loginDeepControl?\n"
    " challenge=VKAS2FB49ZmxbT9gR_FVXzXlVKnKdMIE0t4aJZAI044&uuid=8d073ba4-0c15-\n"
    " 411c-b7af-17e98d0ea9bd&mode=login&redirectTarget=cli\n"
)


def test_the_link_survives_the_line_breaks_and_the_escapes():
    link = signin.extract_link(REAL_OUTPUT)
    assert link == (
        "https://cursor.com/loginDeepControl?"
        "challenge=VKAS2FB49ZmxbT9gR_FVXzXlVKnKdMIE0t4aJZAI044"
        "&uuid=8d073ba4-0c15-411c-b7af-17e98d0ea9bd"
        "&mode=login&redirectTarget=cli")


def test_a_half_link_is_not_good_enough():
    """The naive search returns the first line only. That link does nothing."""
    assert not signin.extract_link(REAL_OUTPUT).endswith("?")


def test_output_with_no_link_gives_nothing():
    assert signin.extract_link(" Starting login process...\n") is None
```

- [ ] **Step 2: Run them and see them fail**

Run: `.venv/bin/pytest tests/test_signin.py -q`
Expected: FAIL with `ImportError: cannot import name 'signin'`.

- [ ] **Step 3: Write the parser**

```python
# nightshift/signin.py
"""The Cursor sign-in flow.

The link that this module reads is a credential: it carries a challenge and a
session id. It stays in memory while the flow runs. It never enters the
database and it never enters a log.
"""
import re
import subprocess
import threading
import time

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
DEADLINE_SECONDS = 180


def extract_link(raw: str) -> str | None:
    """Read the sign-in link out of the CLI output.

    The CLI wraps the link across three lines and mixes ANSI escapes into it.
    A plain search for a URL returns `https://cursor.com/loginDeepControl?`,
    which opens nothing. So the escapes go first, then the wrap.
    """
    clean = ANSI.sub("", raw)
    joined = re.sub(r"\n\s*", "", clean)
    found = re.search(r"https://\S+", joined)
    return found.group(0) if found else None
```

- [ ] **Step 4: Run the tests again**

Run: `.venv/bin/pytest tests/test_signin.py -q`
Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
git add nightshift/signin.py tests/test_signin.py
git commit -m "Read the sign-in link that the CLI breaks across lines"
```

---

## Task 3: The sign-in process

**Files:** Modify `nightshift/signin.py`, `tests/test_signin.py`.

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/test_signin.py
import pathlib


FAKE = pathlib.Path(__file__).parent.parent / "scripts" / "fake-cursor-login"


def test_a_started_flow_reports_the_link(tmp_path):
    flow = signin.Flow(binary=str(FAKE))
    flow.start()
    assert flow.wait_for_link(timeout=10).startswith("https://cursor.com/")
    assert flow.state == "waiting"
    flow.cancel()


def test_the_browser_never_opens_by_itself(tmp_path):
    """NO_OPEN_BROWSER keeps the flow quiet, so nothing appears on screen."""
    flow = signin.Flow(binary=str(FAKE))
    flow.start()
    assert flow.env_used.get("NO_OPEN_BROWSER") == "1"
    flow.cancel()


def test_a_cancelled_flow_stops_the_process():
    flow = signin.Flow(binary=str(FAKE))
    flow.start()
    flow.wait_for_link(timeout=10)
    flow.cancel()
    assert flow.state == "cancelled"
    assert flow.process.poll() is not None


def test_the_link_is_never_part_of_the_public_state():
    """The link is a credential. `status()` feeds the page and the logs."""
    flow = signin.Flow(binary=str(FAKE))
    flow.start()
    link = flow.wait_for_link(timeout=10)
    assert link not in repr(flow.status())
    assert "challenge" not in repr(flow.status())
    flow.cancel()
```

- [ ] **Step 2: Write the fake CLI at `scripts/fake-cursor-login`**

```bash
#!/bin/sh
# A fake `cursor-agent login`. It prints the same wrapped link and it waits.
printf ' Starting login process...\n\n'
printf ' Open a browser and navigate to this link: https://cursor.com/loginDeepControl?\n'
printf ' challenge=TESTCHALLENGE0000&uuid=00000000-0000-0000-0000-000000000000\n'
printf ' &mode=login&redirectTarget=cli\n'
sleep 120
```

Then: `chmod +x scripts/fake-cursor-login`

- [ ] **Step 3: Run the tests and see them fail**

Run: `.venv/bin/pytest tests/test_signin.py -q`
Expected: FAIL with `AttributeError: module 'nightshift.signin' has no attribute 'Flow'`.

- [ ] **Step 4: Write the class**

```python
# add to nightshift/signin.py
class Flow:
    """One sign-in attempt. The link never leaves this object."""

    def __init__(self, binary: str = "cursor-agent"):
        self.binary = binary
        self.process: subprocess.Popen | None = None
        self.state = "idle"      # idle | waiting | done | cancelled | expired
        self.env_used: dict = {}
        self._link: str | None = None
        self._buffer = ""
        self._started_at = 0.0
        self._lock = threading.Lock()

    def start(self) -> None:
        import os
        self.env_used = dict(os.environ, NO_OPEN_BROWSER="1")
        self.process = subprocess.Popen(
            [self.binary, "login"], stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, env=self.env_used)
        self.state = "waiting"
        self._started_at = time.monotonic()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        for line in self.process.stdout:
            with self._lock:
                self._buffer += line
                if self._link is None:
                    self._link = extract_link(self._buffer)
        with self._lock:
            if self.state == "waiting":
                self.state = "done" if self.process.poll() == 0 else "expired"

    def wait_for_link(self, timeout: float = 20) -> str | None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self._lock:
                if self._link:
                    return self._link
            time.sleep(0.2)
        return None

    def link(self) -> str | None:
        with self._lock:
            return self._link

    def status(self) -> dict:
        """What the page may see. It holds no credential on purpose."""
        expired = (self.state == "waiting"
                   and time.monotonic() - self._started_at > DEADLINE_SECONDS)
        if expired:
            self.cancel()
            self.state = "expired"
        return {"state": self.state, "has_link": self._link is not None,
                "seconds": int(time.monotonic() - self._started_at)}

    def cancel(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        if self.state == "waiting":
            self.state = "cancelled"
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/pytest tests/test_signin.py -q`
Expected: `7 passed`.

- [ ] **Step 6: Commit**

```bash
git add nightshift/signin.py tests/test_signin.py scripts/fake-cursor-login
git commit -m "Run the Cursor sign-in without opening a browser"
```

---

## Task 4: The routes

**Files:** Modify `nightshift/web.py`, `tests/test_web.py`.

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/test_web.py
def test_the_machine_room_shows_every_engine(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    body = TestClient(web.make_app(conn, ceiling_usd=20.0)).get("/").text
    for label in ("Claude", "Gemini", "Cursor", "Ollama"):
        assert label in body


def test_sign_in_refuses_a_get(tmp_path):
    """A prefetch of the browser must not start a login."""
    conn = db.connect(tmp_path / "s.db")
    client = TestClient(web.make_app(conn, ceiling_usd=20.0))
    assert client.get("/machines/cursor/login").status_code == 405


def test_sign_in_status_holds_no_credential(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    client = TestClient(web.make_app(conn, ceiling_usd=20.0))
    body = client.get("/machines/cursor/login/status").text
    assert "challenge" not in body
```

- [ ] **Step 2: Run them and see them fail**

Run: `.venv/bin/pytest tests/test_web.py -q`
Expected: FAIL, the labels are absent and the routes give 404.

- [ ] **Step 3: Add the routes inside `make_app`, before `return app`**

```python
    @app.get("/machines/cursor/login/status")
    def signin_status():
        flow = _FLOWS.get("cursor")
        if flow is None:
            return {"state": "idle", "has_link": False, "seconds": 0}
        return flow.status()

    @app.post("/machines/cursor/login")
    def signin_start():
        flow = signin.Flow()
        _FLOWS["cursor"] = flow
        flow.start()
        # The link goes to the page once, and it enters no log and no table.
        return {"link": flow.wait_for_link(timeout=25),
                "state": flow.status()["state"]}

    @app.post("/machines/cursor/login/cancel")
    def signin_cancel():
        flow = _FLOWS.get("cursor")
        if flow:
            flow.cancel()
        return {"state": "cancelled"}
```

Add at the top of `nightshift/web.py`:

```python
from nightshift import backends, jobs, quota, signin

# One flow at a time, in memory. A sign-in link is a credential and it belongs
# in no table.
_FLOWS: dict = {}
```

And add `"engines": backends.check_all()` to the context of the `brief` route.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/pytest tests/test_web.py -q`
Expected: every test passes.

- [ ] **Step 5: Commit**

```bash
git add nightshift/web.py tests/test_web.py
git commit -m "Add the machine room routes"
```

---

## Task 5: The page

**Files:** Rewrite `nightshift/templates/brief.html`.

The look is settled and a working mockup exists at
`docs/mockups/machine-room.html`. Copy its CSS as it stands. Replace its fixed
content with the Jinja loops of the current template, and add the machine room
section that walks `engines`.

Rules for the engine panel:
- A lamp is lit when `e.signed_in` is true, dark when false, and it shows a
  question mark when it is None.
- Each capability shows YES, NO, or UNKNOWN. Never guess.
- The lever appears only when `e.can_sign_in` is true.
- Under the mail work the page states: "Claude is the only engine with a mail
  connector."

- [ ] **Step 1: Copy the mockup CSS into the template**
- [ ] **Step 2: Keep every Jinja block of the current template**
- [ ] **Step 3: Add the machine room loop**
- [ ] **Step 4: Add the sign-in JavaScript: POST, show the link, poll the
      status every three seconds, and stop at three minutes**
- [ ] **Step 5: Run the suite. It must stay green.**
- [ ] **Step 6: Commit**

---

## Done means

- `.venv/bin/pytest -q` passes.
- The page shows the four engines with the state that the cheap checks found.
- A capability with no probe says UNKNOWN instead of claiming anything.
- The Cursor lever gives a full link, with its challenge intact.
- No log file and no table holds that link.
