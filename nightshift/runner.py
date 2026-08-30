# nightshift/runner.py
"""The only part that calls the agent."""
import dataclasses
import json
import pathlib
import subprocess

# A failure inside a run prints on stdout and the exit code can still be 0.
# These are read ONLY when the output is not JSON. A good answer can quote the
# word 401, for example a mail about a 401k plan, and that must not look like a
# failure. Valid JSON always means the run worked.
AUTH_MARKERS = ("failed to authenticate", "oauth access token has expired",
                "401")

# ponytail: a killed run may already have paid for several tool calls, and the
# real number died with the process. Charging zero would blind the governor, so
# this charges a conservative guess. Measured range of one real call on
# 2026-08-30: 0.17 to 0.79 USD.
TIMEOUT_COST_USD = 1.0


@dataclasses.dataclass(frozen=True)
class RunResult:
    ok: bool
    text: str = ""
    cost_usd: float = 0.0
    session_id: str | None = None
    error: str | None = None


def run(prompt: str, *, cwd: pathlib.Path, binary: str = "claude",
        allowed_tools: str | None = None, schema: dict | None = None,
        timeout: int = 900) -> RunResult:
    """Run one headless turn.

    Never pass --bare: bare mode leaves the subscription and asks for an API
    key. `cwd` is explicit because a -p session connects the MCP servers and
    runs the hooks of its working directory with no trust dialog.
    """
    cmd = [binary, "-p", prompt, "--output-format", "json"]
    if allowed_tools:
        cmd += ["--allowedTools", allowed_tools]
    if schema:
        cmd += ["--json-schema", json.dumps(schema)]
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        return RunResult(False, cost_usd=TIMEOUT_COST_USD,
                         error=f"The run passed {timeout} seconds.")

    out = (proc.stdout or "").strip()
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        # Only here do the markers matter: an authentication failure arrives as
        # plain text, never as JSON.
        low = out.lower()
        if any(m in low for m in AUTH_MARKERS):
            return RunResult(False, error=out[:400])
        return RunResult(False, error=f"The output is not JSON: {out[:200]}")
    return RunResult(True, text=data.get("result", ""),
                     cost_usd=float(data.get("total_cost_usd", 0.0)),
                     session_id=data.get("session_id"))
