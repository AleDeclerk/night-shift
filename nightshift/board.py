"""What the week did, read from the events.

Every number here comes from a row that some action wrote. Nothing is derived
from a guess, and a week with no history says so instead of showing zeros: a
zero and "not enough history" look alike and mean the opposite.
"""
import datetime as dt
import sqlite3
import statistics

# Five is the smallest number that means anything for a rate: a rate over two
# items is noise printed as a fact.
MIN_CLOSED_FOR_RATE = 5
MIN_FOUND_TO_BE_ENOUGH = 5
MIN_RATED_FOR_AVERAGE = 3


def week(conn: sqlite3.Connection, now: dt.datetime | None = None,
         days: int = 7) -> dict:
    """The board's numbers for the last `days` days, all read from `events`.
    """
    now = now or dt.datetime.now()
    since = (now - dt.timedelta(days=days)).isoformat()

    reviewed = conn.execute(
        "SELECT count(*) FROM events WHERE kind='item_found' AND at >= ?",
        (since,)).fetchone()[0]
    raised = conn.execute(
        "SELECT count(*) FROM events WHERE kind='item_found' AND at >= ?"
        " AND detail='needs_you'", (since,)).fetchone()[0]

    closed_verbs = [r["verb"] or "?" for r in conn.execute(
        "SELECT verb FROM events WHERE kind='item_closed' AND at >= ?",
        (since,))]
    closed = {}
    for verb in closed_verbs:
        closed[verb] = closed.get(verb, 0) + 1
    total_closed = len(closed_verbs)
    false_alarm_rate = (closed.get("no_era_nada", 0) / total_closed
                        if total_closed >= MIN_CLOSED_FOR_RATE else None)

    hours_to_close = _median_hours_to_close(conn, since)

    spend = {}
    for row in conn.execute(
            "SELECT engine, cost_usd FROM events"
            " WHERE at >= ? AND engine IS NOT NULL", (since,)):
        entry = spend.setdefault(row["engine"], {"cost_usd": 0.0, "calls": 0})
        entry["cost_usd"] += row["cost_usd"] or 0.0
        entry["calls"] += 1

    jobs = {kind: conn.execute(
        "SELECT count(*) FROM events WHERE kind=? AND at >= ?",
        (kind, since)).fetchone()[0] for kind in ("job_done", "job_failed")}

    scores = _scores(conn, since)

    return {
        "reviewed": reviewed, "raised": raised, "closed": closed,
        "false_alarm_rate": false_alarm_rate,
        "hours_to_close": hours_to_close, "spend": spend, "jobs": jobs,
        "scores": scores, "enough": reviewed >= MIN_FOUND_TO_BE_ENOUGH,
    }


def _median_hours_to_close(conn: sqlite3.Connection, since: str) -> float | None:
    """The wait, in hours, between the moment an item was found and the
    moment it was closed, for every item closed since `since`."""
    rows = conn.execute(
        "SELECT closed.at AS closed_at, found.at AS found_at"
        " FROM events AS closed JOIN events AS found"
        " ON found.item_id = closed.item_id AND found.kind = 'item_found'"
        " WHERE closed.kind = 'item_closed' AND closed.at >= ?", (since,))
    hours = []
    for row in rows:
        found = dt.datetime.fromisoformat(row["found_at"])
        closed = dt.datetime.fromisoformat(row["closed_at"])
        hours.append((closed - found).total_seconds() / 3600)
    return statistics.median(hours) if hours else None


def _scores(conn: sqlite3.Connection, since: str) -> dict:
    """How many ratings the week holds, and their average. Fewer than three
    is not enough to trust: one glowing or one harsh score would swing it."""
    # One score for each item, the last one given. A second rating replaces
    # the first, so counting both would weigh one item twice.
    scores = [int(r["detail"]) for r in conn.execute(
        "SELECT detail FROM events e WHERE kind='item_rated' AND at >= ?"
        " AND id = (SELECT max(id) FROM events WHERE kind='item_rated'"
        "           AND item_id = e.item_id AND at >= ?)",
        (since, since))]
    average = (statistics.mean(scores)
              if len(scores) >= MIN_RATED_FOR_AVERAGE else None)
    return {"count": len(scores), "average": average}


def by_day(conn: sqlite3.Connection, now: dt.datetime | None = None,
           days: int = 7) -> list:
    """One row per calendar day, oldest first, ending today: how many items
    were found and how much was spent, so the page can draw a bar per day
    with no library."""
    now = now or dt.datetime.now()
    today = now.date()
    rows = []
    for offset in range(days - 1, -1, -1):
        day = today - dt.timedelta(days=offset)
        start = dt.datetime.combine(day, dt.time.min).isoformat()
        end = dt.datetime.combine(day + dt.timedelta(days=1),
                                  dt.time.min).isoformat()
        found = conn.execute(
            "SELECT count(*) FROM events WHERE kind='item_found'"
            " AND at >= ? AND at < ?", (start, end)).fetchone()[0]
        spend = conn.execute(
            "SELECT coalesce(sum(cost_usd), 0) FROM events"
            " WHERE at >= ? AND at < ?", (start, end)).fetchone()[0]
        rows.append({"date": day.isoformat(), "found": found,
                    "spend": float(spend)})
    return rows
