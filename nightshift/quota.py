"""The governor. It counts the spend of this system and it stops the system."""
import dataclasses
import datetime as dt
import sqlite3


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
