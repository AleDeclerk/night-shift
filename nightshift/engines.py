"""Run one prompt on the engine that the user chose.

Only Claude can force a schema, so every other engine gets the shape asked in
words and a tolerant parse. A local model that answers well but formats badly
must not lose its work.
"""
import dataclasses
import json
import os
import shutil
import pathlib
import re
import subprocess

from nightshift import backends

# "auto" walks the cascade of nightshift.cascade instead of naming one CLI.
# It is not itself a command: command_for and engines.run never see it, since
# a caller resolves it to a concrete engine first.
JOB_ENGINES = ("auto", "claude", "cursor", "ollama")   # gemini cannot run
MAIL_ENGINES = ("auto", "claude", "cursor", "ollama")
DEFAULT_ENGINE = "claude"

SETTINGS_KEY = "job_engine"
MAIL_SETTINGS_KEY = "mail_engine"

# Case-insensitive markers of a call that failed, even when the process exits
# clean and prints something that looks like an answer. Defined once, in
# backends, because two copies drift and the drift stays silent.
FAILURE_MARKERS = backends.FAILURE_MARKERS
_FAILURE_MARKERS = FAILURE_MARKERS

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)
_BRACE_RE = re.compile(r"\{.*\}", re.DOTALL)


@dataclasses.dataclass(frozen=True)
class EngineRun:
    ok: bool
    text: str = ""
    cost_usd: float = 0.0
    error: str | None = None



# launchd starts with a PATH that holds neither Homebrew nor ~/.local/bin, so a
# bare name does not resolve. On 2026-09-01 a scheduled job died with
# `Cannot start cursor-agent: No such file or directory` while doing real work.
# The same bug was fixed for `claude` alone, and it bit the next engine, so this
# resolves every one of them in one place.
FALLBACK_DIRS = ("/opt/homebrew/bin", "/usr/local/bin",
                 str(pathlib.Path.home() / ".local/bin"))


def binary_for(name: str) -> str:
    """An absolute path to a CLI. The plain name when nothing resolves, so the
    error names the binary that was missing."""
    found = shutil.which(name)
    if found:
        return found
    for folder in FALLBACK_DIRS:
        candidate = pathlib.Path(folder) / name
        if candidate.exists():
            return str(candidate)
    return name


def command_for(engine: str, prompt: str,
                model: str | None = None) -> list[str] | None:
    """The exact command line for one engine, or None when it cannot run.

    `model` names a step of the cascade to cursor-agent with `--model`. Only
    cursor takes one: claude has no flag for it, and the local model is
    whatever `dsh` already points at, so a model given to either changes
    nothing.
    """
    if engine == "claude":
        return [binary_for("claude"), "-p", prompt, "--output-format", "json"]
    if engine == "cursor":
        command = [binary_for("cursor-agent"), "-p", prompt,
                   "--output-format", "json",
                   "--force"]
        if model:
            command += ["--model", model]
        return command
    if engine == "ollama":
        return [binary_for("dsh"), "--profile", "headless", prompt]
    return None


def _read_answer(raw: str) -> tuple[str, float]:
    """Try json.loads, then `result`, then `response`, then the raw text."""
    raw = raw.strip()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw, 0.0
    if not isinstance(data, dict):
        return raw, 0.0
    text = data.get("result") or data.get("response") or raw
    cost = float(data.get("total_cost_usd") or 0.0)
    return text, cost


def run(prompt: str, *, engine: str, cwd, timeout: int = 900,
        model: str | None = None) -> EngineRun:
    """Run one headless turn on `engine`. Never raises."""
    command = command_for(engine, prompt, model)
    if command is None:
        return EngineRun(False, error=f"Unknown engine: {engine}")

    env = {**os.environ, **backends.probe_env(engine)}
    try:
        proc = subprocess.run(command, cwd=cwd, capture_output=True,
                              text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return EngineRun(False, error=f"{engine} passed {timeout} seconds.")
    except OSError as exc:
        return EngineRun(False, error=f"Cannot start {command[0]}: {exc}")

    raw = (proc.stdout or "") + (proc.stderr or "")
    text, cost = _read_answer(raw)
    if any(marker in text.lower() for marker in _FAILURE_MARKERS):
        return EngineRun(False, text=text, cost_usd=cost, error=text[:400])
    return EngineRun(True, text=text, cost_usd=cost)


def _as_job_dict(candidate: str) -> dict | None:
    try:
        data = json.loads(candidate)
    except (json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) and "finished" in data else None


def parse_job_answer(text: str) -> dict:
    """Tolerant read of a job answer that may not be clean JSON.

    A local model very often wraps its JSON in ``` fences or a sentence, so
    this tries progressively looser extractions before giving up. Work that
    arrived as prose, with no JSON in it anywhere, is still work: it becomes a
    finished job with that prose as its summary, never a dropped answer.
    """
    if not text or not text.strip():
        return {"finished": False, "question": "The engine answered nothing."}

    stripped = text.strip()
    candidates = [stripped]
    candidates += [m.group(1).strip() for m in _FENCE_RE.finditer(text)]
    brace = _BRACE_RE.search(text)
    if brace:
        candidates.append(brace.group(0))

    for candidate in candidates:
        data = _as_job_dict(candidate)
        if data is not None:
            return data

    return {"finished": True, "summary": stripped[:2000]}


def _get_setting(conn, key: str) -> str:
    row = conn.execute(
        "SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row is not None else DEFAULT_ENGINE


def _set_setting(conn, key: str, name: str, choices: tuple[str, ...]) -> None:
    if name not in choices:
        raise ValueError(f"Unknown engine: {name}")
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, name))
    conn.commit()


def get_engine(conn) -> str:
    return _get_setting(conn, SETTINGS_KEY)


def set_engine(conn, name: str) -> None:
    _set_setting(conn, SETTINGS_KEY, name, JOB_ENGINES)


def get_mail_engine(conn) -> str:
    """Which engine composes the reply. Claude still fetches and saves every
    draft: only it holds the Gmail connector."""
    return _get_setting(conn, MAIL_SETTINGS_KEY)


def set_mail_engine(conn, name: str) -> None:
    _set_setting(conn, MAIL_SETTINGS_KEY, name, MAIL_ENGINES)
