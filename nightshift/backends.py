"""What each engine can do, measured with cheap commands.

A cheap check spends no quota and takes about a second. It reports what the
machine claims. A real probe, which costs 0.17 to 0.34 USD, is a separate
feature that runs only when the user asks for it.
"""
import dataclasses
import datetime as dt
import json
import os
import pathlib
import re
import shutil
import subprocess
import threading
import time

CHEAP_TIMEOUT = 8

# Measured on 2026-08-31: `claude mcp list` takes 3.9 seconds, because it
# health-checks ten servers. The other four commands add one more second. That
# is cheap in quota and expensive in time, so the answer is held for a while.
# The morning page must draw at once, not after five seconds.
CACHE_SECONDS = 60
_CACHE = {"at": 0.0, "engines": None}
_CACHE_LOCK = threading.Lock()


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


# Cursor approves an MCP server per directory, so a check that runs somewhere
# else measures a different machine than the one the runner uses.
WORKSPACE = pathlib.Path.home() / ".night-shift" / "workspace"


def _shell(args, runner=None, cwd=None) -> str:
    """Run a cheap command and give its text."""
    if runner is not None:
        return runner(args, cwd=cwd)
    try:
        out = subprocess.run(args, capture_output=True, text=True,
                             timeout=CHEAP_TIMEOUT, cwd=cwd)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TimeoutError(str(exc)) from exc
    return (out.stdout or "") + (out.stderr or "")


def _safe(args, runner=None, cwd=None) -> str | None:
    try:
        return _shell(args, runner, cwd)
    except (TimeoutError, OSError):
        return None


def _claude(runner) -> Engine:
    auth = _safe(["claude", "auth", "status"], runner)
    signed = None
    if auth:
        try:
            signed = bool(json.loads(auth).get("loggedIn"))
        except (json.JSONDecodeError, AttributeError):
            signed = None
    mcp = _safe(["claude", "mcp", "list"], runner) or ""
    mail = "Gmail" in mcp
    return Engine("claude", "Claude", "Max subscription",
                  installed=bool(shutil.which("claude")), signed_in=signed,
                  sees_mail=mail, forced_schema=True,
                  mail_capable=mail and signed is not False, can_sign_in=False)


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
    # A connector counts only when the server answers. `gemini mcp list` marks
    # a live one as Connected.
    mail = "gmail" in mcp.lower() and "connected" in mcp.lower()
    return Engine("gemini", "Gemini", "Personal Google account",
                  installed=bool(shutil.which("gemini")), signed_in=signed,
                  sees_mail=mail, forced_schema=False,
                  mail_capable=mail and signed is True, can_sign_in=False)


def _cursor(runner, workspace=None) -> Engine:
    where = str(workspace or WORKSPACE)
    out = _safe(["cursor-agent", "status"], runner, cwd=where)
    signed = None if out is None else "not logged in" not in out.lower()
    mcp = _safe(["cursor-agent", "mcp", "list"], runner, cwd=where) or ""
    # `cursor-agent mcp list` marks an approved and loaded server as ready.
    mail = "gmail" in mcp.lower() and "ready" in mcp.lower()
    return Engine("cursor", "Cursor", "cursor-agent",
                  installed=bool(shutil.which("cursor-agent")),
                  signed_in=signed, sees_mail=mail, forced_schema=False,
                  mail_capable=mail and signed is True,
                  can_sign_in=signed is not True)


def _ollama(runner) -> Engine:
    out = _safe(["ollama", "list"], runner)
    models = 0 if out is None else max(0, len(out.strip().splitlines()) - 1)
    return Engine("ollama", "Ollama", f"{models} local models",
                  installed=bool(shutil.which("ollama")),
                  signed_in=None if out is None else True,
                  sees_mail=False, forced_schema=True,
                  mail_capable=False, can_sign_in=False)


def _fresh(runner, workspace=None) -> list[Engine]:
    return [_claude(runner), _gemini(runner), _cursor(runner, workspace),
            _ollama(runner)]


def invalidate() -> None:
    """Drop the held answer. A sign-in must show on the page at once."""
    with _CACHE_LOCK:
        _CACHE["engines"] = None


def check_all(runner=None, use_cache: bool | None = None,
              workspace=None) -> list[Engine]:
    """An injected runner means "look again", so it skips the cache by
    default. Otherwise one test would hand its answer to the next one."""
    if use_cache is None:
        use_cache = runner is None
    if not use_cache:
        return _fresh(runner, workspace)
    with _CACHE_LOCK:
        held = _CACHE["engines"]
        if held is not None and time.monotonic() - _CACHE["at"] < CACHE_SECONDS:
            return held
    engines = _fresh(runner, workspace)
    with _CACHE_LOCK:
        _CACHE.update(at=time.monotonic(), engines=engines)
    return engines


def warm_up() -> None:
    """Fill the cache in the background, so the first page waits for nothing."""
    threading.Thread(target=lambda: check_all(), daemon=True).start()


# --- The real probe. Costs 0.17 to 0.34 USD, only runs on request. ---------

PROBE_PROMPT = (
    "Use your gmail tool to list the labels of this mailbox. "
    "Answer with the single word MAIL-OK when the call works. "
    "Answer with the single word NO-MAIL when you have no gmail tool, or when "
    "the call fails. Answer with nothing else.")

# Each engine speaks its own CLI. None of them takes an API key: this project
# runs on subscriptions only.
PROBE_COMMANDS = {
    "claude": ["claude", "-p", PROBE_PROMPT, "--output-format", "json"],
    "gemini": ["gemini", "-p", PROBE_PROMPT, "-o", "json",
               "--approval-mode", "yolo"],
    "cursor": ["cursor-agent", "-p", PROBE_PROMPT, "--output-format", "json",
               "--approve-mcps", "--force"],
    # `ollama run` only writes text. The DeepSeek Harness gives the same local
    # model a sandbox, shell tools and a permission policy, and the patch of
    # this machine already points it at Ollama. Measured on 2026-08-31: it
    # answered one word in 56 seconds, against about 3 for Claude.
    "ollama": ["dsh", "--profile", "headless", PROBE_PROMPT],
}

# Ollama authenticates nobody, but the OpenAI-compatible client that the
# harness uses refuses to start without a value. This buys no service and it
# meters nothing: the call never leaves this Mac. It is not the API key that
# rule 1 of the specification forbids.
PROBE_ENV = {"ollama": {"OLLAMA_API_KEY": "ollama"}}


def probe_env(engine: str) -> dict:
    return dict(PROBE_ENV.get(engine, {}))
PROBE_TIMEOUT = 180

# Case-insensitive markers of a call that failed, even when the process exits
# clean and prints something. Configuration lies; these strings are what the
# real call says when it could not do the work.
_FAILURE_MARKERS = (
    "ineligibletier", "incompatible auth server", "not logged in",
    "failed to authenticate", "oauth access token has expired")

# A detail is for a person reading a status page, never a place to carry a
# login link. Strip the query string off any URL before it is kept.
_URL_QUERY = re.compile(r"(https?://\S+?)\?\S+")


@dataclasses.dataclass(frozen=True)
class ProbeResult:
    engine: str
    ok: bool           # the engine answered at all
    can_mail: bool     # the engine read the mailbox
    cost_usd: float
    detail: str        # short, for the page. Never a credential.


def _scrub(text: str) -> str:
    return _URL_QUERY.sub(r"\1", text)


def probe(engine: str, runner=None, workspace=None) -> ProbeResult:
    """Make the one real call. What comes back is evidence; what the engine
    claims about itself, elsewhere, is not."""
    command = PROBE_COMMANDS.get(engine)
    if command is None:
        return ProbeResult(engine, False, False, 0.0, "unknown engine")

    cwd = workspace or WORKSPACE
    try:
        if runner is not None:
            raw = runner(command, cwd=cwd)
        else:
            out = subprocess.run(command, cwd=cwd, capture_output=True,
                                 text=True, timeout=PROBE_TIMEOUT,
                                 env={**os.environ, **probe_env(engine)})
            raw = (out.stdout or "") + (out.stderr or "")
    except OSError as exc:
        return ProbeResult(engine, False, False, 0.0,
                           f"could not run {engine}: {exc}")
    except subprocess.TimeoutExpired:
        return ProbeResult(engine, False, False, 0.0,
                           f"{engine} did not answer within {PROBE_TIMEOUT}s")

    cost = 0.0
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        text = raw
    else:
        text = data.get("result") or data.get("response") or raw
        cost = float(data.get("total_cost_usd") or 0.0)

    lowered = text.lower()
    can_mail = "MAIL-OK" in text
    ok = bool(text.strip()) and not any(
        marker in lowered for marker in _FAILURE_MARKERS)
    detail = _scrub(text).strip()[:200]
    return ProbeResult(engine, ok, can_mail, cost, detail)


def save_probe(conn, result: ProbeResult) -> None:
    conn.execute(
        "INSERT INTO probes (engine, at, ok, can_mail, cost_usd, detail)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (result.engine, dt.datetime.now().isoformat(), int(result.ok),
         int(result.can_mail), result.cost_usd, result.detail))
    conn.commit()


def last_probes(conn) -> dict:
    """The newest probe row of each engine, keyed by engine name."""
    rows = conn.execute(
        "SELECT * FROM probes WHERE id IN"
        " (SELECT MAX(id) FROM probes GROUP BY engine)").fetchall()
    return {row["engine"]: row for row in rows}
