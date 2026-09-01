"""The governor. It counts the spend of this system and it stops the system."""
import dataclasses
import datetime as dt
import json
import os
import sqlite3

from nightshift import life, usage

# Measured on 2026-08-30 with three real cycles: a cycle that finds nothing
# costs 0.87 USD of equivalent spend, and each draft adds about 0.80. One cycle
# each day with three drafts is about 3.3 USD, so a week lands near 23. The
# ceiling sits a little under that on purpose: it must bite before the week
# ends if the mail volume grows.
DEFAULT_CEILING_USD = 20.0


def ceiling_usd() -> float:
    """The weekly ceiling, read where the user sets it.

    Three copies of 20.0 lived in the scripts and in the cascade, and only
    two of them read the variable. The budget card told the user to raise
    NIGHTSHIFT_CEILING_USD, and the page went on judging against its own
    number. This is the one place that reads it, at call time, so a new
    value needs no restart.
    """
    named = os.environ.get("NIGHTSHIFT_CEILING_USD")
    try:
        return float(named) if named else DEFAULT_CEILING_USD
    except ValueError:      # a bad value must not stop the cycle at 06:30
        return DEFAULT_CEILING_USD


@dataclasses.dataclass(frozen=True)
class Decision:
    allowed: bool
    spent_usd: float
    reason: str


# ponytail: the Anthropic weekly limit resets on a fixed day that the account
# holds, and no API reports it. So this counts an ISO week that starts on
# Monday. If the real reset day appears later, change week_start only.
def week_start(now: dt.datetime) -> dt.datetime:
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight - dt.timedelta(days=midnight.weekday())


def spent_this_week(conn: sqlite3.Connection, now: dt.datetime) -> float:
    row = conn.execute(
        "SELECT coalesce(sum(cost_usd), 0) FROM runs WHERE started_at >= ?",
        (week_start(now).isoformat(),)).fetchone()
    return float(row[0])


def may_run(conn: sqlite3.Connection, now: dt.datetime,
            ceiling_usd: float) -> Decision:
    spent = spent_this_week(conn, now)
    if spent >= ceiling_usd:
        return Decision(False, spent,
                        f"This week the system spent {spent} of {ceiling_usd} USD.")
    return Decision(True, spent,
                    f"This week the system spent {spent} of {ceiling_usd} USD.")


# --- the real quota, read from the CLI instead of counted ----------------

# How long a reading is worth reading. The week moves, so an old number
# describes a room that is gone. A stale reading is no reading, and the
# governor then counts, which is the careful path.
USAGE_MAX_AGE_MINUTES = 90

# `usage.read` gives None for a CLI that could not run and for an answer
# that could not be parsed, and it tells the two apart nowhere. So this is
# what the record can honestly say.
NO_READING = "claude -p /usage gave no answer that could be read"


def _as_json(reading: usage.Usage) -> str:
    return json.dumps({
        "week_pct": reading.week_pct,
        "week_resets": reading.week_resets.isoformat(),
        "session_pct": reading.session_pct,
        "session_resets": reading.session_resets.isoformat()})


def read_usage(conn: sqlite3.Connection, *, cwd, now: dt.datetime,
               binary: str = "claude") -> usage.Usage | None:
    """Ask the CLI for the real quota and keep the answer.

    One call takes about three seconds, so a page load may never make it.
    The tick and the cycle make it once, and every later reader rebuilds the
    numbers from the event with `last_usage`.
    """
    reading = usage.read(cwd=cwd, binary=binary, now=now)
    if reading is None:
        life.record(conn, "usage_unavailable", detail=NO_READING, now=now)
        return None
    life.record(conn, "usage_read", detail=_as_json(reading), now=now)
    return reading


def last_usage(conn: sqlite3.Connection, now: dt.datetime,
               max_age_minutes: int = USAGE_MAX_AGE_MINUTES
               ) -> usage.Usage | None:
    """The newest reading, while it is still fresh enough to mean something.

    Past `max_age_minutes` this gives None, and the caller falls back to
    counting: the week moves under an old number, and rule 1 of the design
    says a reading the system cannot trust is not permission.
    """
    row = conn.execute(
        "SELECT at, detail FROM events WHERE kind='usage_read'"
        " ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        return None
    try:
        at = dt.datetime.fromisoformat(row["at"])
        data = json.loads(row["detail"] or "")
        reading = usage.Usage(
            week_pct=int(data["week_pct"]),
            week_resets=dt.datetime.fromisoformat(data["week_resets"]),
            session_pct=int(data["session_pct"]),
            session_resets=dt.datetime.fromisoformat(data["session_resets"]))
    except (KeyError, TypeError, ValueError):
        return None      # a row this cannot read is a row that says nothing
    if now - at > dt.timedelta(minutes=max_age_minutes):
        return None
    return reading
