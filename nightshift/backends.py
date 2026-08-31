"""What each engine can do, measured with cheap commands.

A cheap check spends no quota and takes about a second. It reports what the
machine claims. A real probe, which costs 0.17 to 0.34 USD, is a separate
feature that runs only when the user asks for it.
"""
import dataclasses
import json
import pathlib
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
