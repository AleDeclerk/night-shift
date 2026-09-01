"""The row that every spend lands in.

`quota.spent_this_week` reads `runs.cost_usd` and nothing else, so a call
that opens no run is a call the weekly ceiling never sees. The cycle owned
these two functions in private, and the tick could not reach them: it ran
jobs and recorded what it spent in an event that neither governor reads.
"""
import datetime as dt
import sqlite3

from nightshift import life


def start(conn: sqlite3.Connection, now: dt.datetime, kind: str) -> int:
    """Open a run of `kind`: "mail" for the cycle, "tick" for the queue,
    "probe" for one real call to an engine."""
    cur = conn.execute("INSERT INTO runs (started_at, kind) VALUES (?,?)",
                       (now.isoformat(), kind))
    conn.commit()
    return cur.lastrowid


def end(conn: sqlite3.Connection, run_id: int, ok: bool, cost: float = 0.0,
        error: str | None = None, now: dt.datetime | None = None) -> None:
    """Close a run and mirror it in one event, on every exit path.

    `now` travels with the event: the cascade reads `events.at` to decide
    whether an engine has room, so the record must share the clock of its
    caller instead of reading its own.

    The event is `cycle_ran` whatever the kind of the run, because the weekly
    board already reads that name and it means "a run ended".
    """
    conn.execute(
        "UPDATE runs SET finished_at=?, ok=?, cost_usd=?, error=? WHERE id=?",
        (dt.datetime.now().isoformat(), 1 if ok else 0, cost, error, run_id))
    conn.commit()
    life.record(conn, "cycle_ran", cost_usd=cost, detail=error, now=now)
