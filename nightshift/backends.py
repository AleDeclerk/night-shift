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
    """Run a cheap command and give its text."""
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
    if auth:
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
