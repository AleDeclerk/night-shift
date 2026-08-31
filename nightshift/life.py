"""The verbs that close an item, and the events they leave behind.

An event is the record. The `state` column is a copy kept for speed, because a
page that replays every event to draw one list is slow. A state that disagrees
with the events is a bug, and a test proves they agree.
"""
import datetime as dt
import sqlite3

# verb -> (state it leaves, the reason it goes in the event detail)
VERBS = {
    "listo":       ("done",      "draft_used"),
    "lo_hago_yo":  ("done",      "by_hand"),
    "no_era_nada": ("dismissed", "false_alarm"),
    "manana":      ("snoozed",   "later"),
    "rehacer":     ("pending",   "redo"),
}

# A state that closes an item, for the two fields that only a close sets.
_CLOSED_STATES = ("done", "dismissed")


def record(conn: sqlite3.Connection, kind: str, *, item_id: int | None = None,
           job_id: int | None = None, verb: str | None = None,
           engine: str | None = None, cost_usd: float = 0.0,
           detail: str | None = None) -> int:
    """Insert one event and commit. The event is the record, so a caller
    that forgets to commit would leave work that looks done but is not."""
    cur = conn.execute(
        "INSERT INTO events (at, kind, item_id, job_id, verb, engine,"
        " cost_usd, detail) VALUES (?,?,?,?,?,?,?,?)",
        (dt.datetime.now().isoformat(), kind, item_id, job_id, verb, engine,
         cost_usd, detail))
    conn.commit()
    return cur.lastrowid


def apply_verb(conn: sqlite3.Connection, item_id: int, verb: str, *,
               now: dt.datetime | None = None) -> str:
    """Close an item, snooze it, or send it back. Refuses an unknown verb
    and changes nothing. The event is written before this returns: a button
    must never report work that did not happen."""
    if verb not in VERBS:
        raise ValueError(f"Unknown verb: {verb}")
    state, reason = VERBS[verb]
    now = now or dt.datetime.now()
    closed_at = now.isoformat() if state in _CLOSED_STATES else None
    snoozed_until = (now + dt.timedelta(days=1)).isoformat() \
        if verb == "manana" else None

    conn.execute(
        "UPDATE items SET state=?, closed_at=?, snoozed_until=? WHERE id=?",
        (state, closed_at, snoozed_until, item_id))
    conn.commit()
    record(conn, "item_closed", item_id=item_id, verb=verb, detail=reason)
    return state


def state_from_events(conn: sqlite3.Connection, item_id: int) -> str:
    """The state, derived from the events alone, with no read of the column.
    A test uses this to prove the column and the record agree."""
    row = conn.execute(
        "SELECT verb FROM events WHERE item_id=? AND kind='item_closed'"
        " ORDER BY id DESC LIMIT 1", (item_id,)).fetchone()
    if row is None:
        return "pending"
    state, _ = VERBS.get(row["verb"], ("pending", None))
    return state


def open_items(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """What belongs in `Pendiente`: a message that needed an answer and is
    still pending, plus one that was snoozed until a time now in the past.
    `no_action` mail is never part of this life cycle."""
    now = dt.datetime.now().isoformat()
    return conn.execute(
        "SELECT * FROM items WHERE bucket='needs_you' AND ("
        " state='pending' OR"
        " (state='snoozed' AND snoozed_until IS NOT NULL"
        "  AND snoozed_until <= ?)"
        ") ORDER BY id DESC", (now,)).fetchall()


def closed_items(conn: sqlite3.Connection, limit: int = 50) -> list[sqlite3.Row]:
    """What belongs in `Ya revisado`, newest first, each row carrying the
    verb that closed it."""
    return conn.execute(
        "SELECT items.*, ("
        " SELECT verb FROM events WHERE events.item_id = items.id"
        " AND events.kind = 'item_closed' ORDER BY events.id DESC LIMIT 1"
        ") AS verb"
        " FROM items WHERE bucket='needs_you' AND state IN ('done','dismissed')"
        " ORDER BY closed_at DESC, id DESC LIMIT ?", (limit,)).fetchall()


def false_alarm_rate(conn: sqlite3.Connection, days: int = 14) -> dict:
    """What the triage raised in the window, and how much of it a person
    later called a false alarm. The weekly board of the next phase reads
    this to move the prompt on evidence, not on impressions."""
    since = (dt.datetime.now() - dt.timedelta(days=days)).isoformat()
    raised = conn.execute(
        "SELECT count(*) FROM items WHERE bucket='needs_you'"
        " AND created_at >= ?", (since,)).fetchone()[0]
    false = conn.execute(
        "SELECT count(*) FROM items JOIN events"
        " ON events.item_id = items.id"
        " WHERE items.bucket='needs_you' AND items.created_at >= ?"
        " AND events.kind='item_closed' AND events.verb='no_era_nada'",
        (since,)).fetchone()[0]
    return {"raised": raised, "false": false}
