"""The governor. It counts the spend of this system and it stops the system."""
import dataclasses
import datetime as dt
import os
import sqlite3

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
