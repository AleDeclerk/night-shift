"""Which engine takes the next call.

No command reports how much quota a subscription has left, so this counts what
this system spent against a ceiling the user set. It never claims to know the
real balance.
"""
import dataclasses
import datetime as dt
import sqlite3

from nightshift import backends, quota


@dataclasses.dataclass(frozen=True)
class Step:
    name: str          # "claude" | "grok" | "flash" | "local"
    engine: str        # the CLI: "claude" | "cursor" | "ollama"
    model: str | None  # the --model value, None for claude and ollama
    unit: str           # "usd" for claude, "calls" for cursor, "none" for local
    ceiling: float       # 20.0 usd, 60 calls, 0 for the local one


# Grok and Flash both speak through cursor-agent and share one subscription,
# so `has_room` counts their spend together, under the CLI name "cursor", not
# once per step. Only the model asked of cursor-agent tells them apart.
LADDER = (
    Step("claude", "claude", None, "usd", 20.0),
    Step("grok", "cursor", "cursor-grok-4.6-high-fast", "calls", 60),
    Step("flash", "cursor", "gemini-3.7-flash-high", "calls", 60),
    Step("local", "ollama", None, "none", 0),
)

_BY_NAME = {step.name: step for step in LADDER}


def reserve_for(now: dt.datetime, ceiling: float) -> float:
    """One tenth of the ceiling for each day left before the Monday reset.

    Monday leaves 6 days, Sunday leaves 0: section 4 of the design. The
    allowance opens as the week goes on, because early on the system does not
    know what mail is still coming.
    """
    days_left = 6 - now.weekday()
    return ceiling * days_left / 10


def spent_by(conn: sqlite3.Connection, cli: str, now: dt.datetime) -> float:
    """What this system spent this week on one CLI, read from the events
    each call already leaves behind.

    Claude reports a real cost on every call, so this sums it. Cursor reports
    tokens and no cost, so this counts calls instead: a number that needs no
    translation. The local engine never spends anything.
    """
    if cli == "ollama":
        return 0.0
    start = quota.week_start(now).isoformat()
    if cli == "claude":
        row = conn.execute(
            "SELECT coalesce(sum(cost_usd), 0) FROM events"
            " WHERE engine = ? AND at >= ?", (cli, start)).fetchone()
        return float(row[0])
    row = conn.execute(
        "SELECT count(*) FROM events WHERE engine = ? AND at >= ?",
        (cli, start)).fetchone()
    return float(row[0])


def has_room(conn: sqlite3.Connection, step: Step,
             now: dt.datetime) -> tuple[bool, str]:
    """Whether `step` may take the next call this week, and why."""
    if step.unit == "none":
        return True, "the local engine has no ceiling"

    spent = spent_by(conn, step.engine, now)
    limit = step.ceiling
    note = ""
    if step.unit == "usd":
        reserve = reserve_for(now, step.ceiling)
        limit = step.ceiling - reserve
        note = f", today's reserve holds back {reserve:.2f}"

    ok = spent < limit
    reason = (f"{step.name} spent {spent:.2f} of {step.ceiling:.2f} "
             f"{step.unit}{note}")
    return ok, reason


def choose(conn: sqlite3.Connection, now: dt.datetime, *,
           forced: str | None = None, probes=None,
           engines=None) -> tuple["Step", list[str]]:
    """Walk the ladder and give the step that takes the next call, plus the
    reason every step above it was skipped.

    `forced` names a step directly and skips the walk: that is how the user
    overrides a bad automatic choice by hand. `probes` and `engines` let a
    caller inject what it already knows; left out, this asks the real
    machines, which a test must never do by accident.
    """
    if forced is not None and forced in _BY_NAME:
        return _BY_NAME[forced], []

    probes = backends.last_probes(conn) if probes is None else probes
    engines = backends.check_all() if engines is None else engines
    by_cli = {e.name: e for e in engines}

    skipped = []
    for step in LADDER:
        info = by_cli.get(step.engine)
        if info is None or not info.installed or not info.signed_in:
            skipped.append(f"{step.name}: no session")
            continue
        probe = probes.get(step.engine)
        if probe is not None and not probe["ok"]:
            skipped.append(f"{step.name}: last probe failed")
            continue
        ok, reason = has_room(conn, step, now)
        if not ok:
            skipped.append(f"{step.name}: {reason}")
            continue
        return step, skipped

    # ponytail: every step failed one of its checks, even the local one (say,
    # ollama is not installed on this machine). The local step still takes
    # the work: no API key, no cost, the one step with no way to run dry.
    return LADDER[-1], skipped
