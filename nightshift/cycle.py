"""One cycle: quota, mail, drafts. launchd calls this module."""
import datetime as dt
import sqlite3

from nightshift import engines, jobs, quota


def _start_run(conn, now, kind) -> int:
    cur = conn.execute("INSERT INTO runs (started_at, kind) VALUES (?,?)",
                       (now.isoformat(), kind))
    conn.commit()
    return cur.lastrowid


def _end_run(conn, run_id, ok, cost=0.0, error=None) -> None:
    conn.execute(
        "UPDATE runs SET finished_at=?, ok=?, cost_usd=?, error=? WHERE id=?",
        (dt.datetime.now().isoformat(), 1 if ok else 0, cost, error, run_id))
    conn.commit()


def run_once(conn: sqlite3.Connection, *, runner_module, mail_module,
             now: dt.datetime, ceiling_usd: float, workspace) -> None:
    # The window starts where the last good cycle started, so the system never
    # pays twice to classify the same mail, and a gap of days widens it alone.
    previous = conn.execute(
        "SELECT started_at FROM runs WHERE ok = 1 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    since = previous["started_at"] if previous else None

    run_id = _start_run(conn, now, "mail")

    decision = quota.may_run(conn, now=now, ceiling_usd=ceiling_usd)
    if not decision.allowed:
        _end_run(conn, run_id, False, error=decision.reason)
        return

    # There is no separate health check. It costs as much as the work that it
    # would protect, so the triage itself reports an authentication failure.
    result = mail_module.triage(runner_module, cwd=workspace, since=since)
    spent = result.cost_usd
    if result.error:
        _end_run(conn, run_id, False, cost=spent, error=result.error[:400])
        return

    # Each item commits on its own. One exception in the middle used to throw
    # away every draft already written and every dollar already spent.
    stopped_by_budget = 0
    for item in result.items:
        # The triage reads a 24 hour window and the scheduler runs twice a
        # day, so every message comes back at least once. A message that
        # already has an item keeps its old card and gets no second draft.
        if conn.execute("SELECT 1 FROM items WHERE source_url = ? LIMIT 1",
                        (item.source_url,)).fetchone():
            continue

        body = item.body
        try:
            if item.bucket == "needs_you":
                # The ceiling is checked here too, not only before the cycle.
                # A large inbox on the first run would otherwise spend a whole
                # week of quota before anything stopped it.
                if decision.spent_usd + spent >= ceiling_usd:
                    stopped_by_budget += 1
                else:
                    draft = mail_module.write_draft(runner_module, item,
                                                    cwd=workspace)
                    spent += draft.cost_usd
                    # The trace of the draft rides with the item. Without it
                    # an empty draft looks the same as a written one.
                    mark = "Draft: " if draft.ok else "NO DRAFT. "
                    body = f"{item.body}\n\n{mark}{draft.note}"
            conn.execute(
                "INSERT INTO items (run_id, created_at, bucket, title, body,"
                " source_url, excerpt) VALUES (?,?,?,?,?,?,?)",
                (run_id, now.isoformat(), item.bucket, item.title, body,
                 item.source_url, item.excerpt))
            conn.commit()
        except Exception as exc:  # noqa: BLE001
            _end_run(conn, run_id, False, cost=spent,
                     error=f"{item.title}: {exc}"[:400])
            return

    if stopped_by_budget:
        # A draft that never got written must not look like a message that
        # needed no answer.
        conn.execute(
            "INSERT INTO items (run_id, created_at, bucket, title, body,"
            " source_url) VALUES (?,?,?,?,?,?)",
            (run_id, now.isoformat(), "needs_you",
             f"The budget stopped {stopped_by_budget} drafts",
             "The weekly ceiling was reached. Raise NIGHTSHIFT_CEILING_USD, or "
             "wait for the next week, then run the cycle again.", ""))
        conn.commit()

    # One job for each cycle, and only with budget left. Wrapped like the item
    # loop above: a job that explodes must not lose the cost already spent.
    try:
        if decision.spent_usd + spent < ceiling_usd:
            spent += jobs.run_next(conn, runner_module, workspace,
                                   engine=engines.get_engine(conn))
    except Exception as exc:  # noqa: BLE001
        _end_run(conn, run_id, False, cost=spent, error=f"job: {exc}"[:400])
        return
    _end_run(conn, run_id, True, cost=spent)
