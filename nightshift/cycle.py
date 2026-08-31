"""One cycle: quota, mail, drafts. launchd calls this module."""
import datetime as dt
import sqlite3

from nightshift import cascade, engines, jobs, life, mail, quota


def _start_run(conn, now, kind) -> int:
    cur = conn.execute("INSERT INTO runs (started_at, kind) VALUES (?,?)",
                       (now.isoformat(), kind))
    conn.commit()
    return cur.lastrowid


def _end_run(conn, run_id, ok, cost=0.0, error=None, now=None) -> None:
    """`now` travels with the event: the cascade reads `events.at` to decide
    whether an engine has room, so the record must share the clock of the
    cycle instead of reading its own."""
    conn.execute(
        "UPDATE runs SET finished_at=?, ok=?, cost_usd=?, error=? WHERE id=?",
        (dt.datetime.now().isoformat(), 1 if ok else 0, cost, error, run_id))
    conn.commit()
    # One `cycle_ran` event per run, on every exit of this function, so the
    # weekly board reads the same history whether the cycle finished clean or
    # stopped early on quota or on a broken tool.
    life.record(conn, "cycle_ran", cost_usd=cost, detail=error, now=now)


def run_once(conn: sqlite3.Connection, *, runner_module, mail_module,
             now: dt.datetime, ceiling_usd: float, workspace,
             mail_engine: str = "claude") -> None:
    # The window starts where the last good cycle started, so the system never
    # pays twice to classify the same mail, and a gap of days widens it alone.
    previous = conn.execute(
        "SELECT started_at FROM runs WHERE ok = 1 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    since = previous["started_at"] if previous else None

    run_id = _start_run(conn, now, "mail")

    decision = quota.may_run(conn, now=now, ceiling_usd=ceiling_usd)
    if not decision.allowed:
        _end_run(conn, run_id, False, error=decision.reason, now=now)
        return

    # Rule 2 of the cascade design: only Claude holds the Gmail connector, so
    # the fetch never walks the ladder. When Claude has no room this is not a
    # failure, it is the budget running its course, so the cycle ends clean
    # and waits for the next day.
    room_ok, room_reason = cascade.has_room(conn, cascade.LADDER[0], now)
    if not room_ok:
        _end_run(conn, run_id, True, error=room_reason, now=now)
        return

    # There is no separate health check. It costs as much as the work that it
    # would protect, so the triage itself reports an authentication failure.
    result = mail_module.triage(runner_module, cwd=workspace, since=since)
    spent = result.cost_usd
    if result.error:
        _end_run(conn, run_id, False, cost=spent, error=result.error[:400], now=now)
        return

    # Which engine composes the reply. A fixed name runs exactly as before;
    # "auto" walks the ladder once for the whole cycle, so every item in it
    # gets the same choice and the page can name the one engine that worked.
    compose_engine, compose_model = mail_engine, None
    if mail_engine == "auto":
        step, _fallen_from = cascade.choose(conn, now)
        compose_engine, compose_model = step.engine, step.model

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
        draft_ok, draft_cost = False, 0.0
        try:
            if item.bucket == "needs_you":
                # The ceiling is checked here too, not only before the cycle.
                # A large inbox on the first run would otherwise spend a whole
                # week of quota before anything stopped it.
                if decision.spent_usd + spent >= ceiling_usd:
                    stopped_by_budget += 1
                else:
                    cost, ok, note = mail.reply_cost_and_trace(
                        mail_module, runner_module, item, engine=compose_engine,
                        model=compose_model, cwd=workspace)
                    spent += cost
                    draft_ok, draft_cost = ok, cost
                    # The trace of the draft rides with the item. Without it
                    # an empty draft looks the same as a written one.
                    mark = "Draft: " if ok else "NO DRAFT. "
                    body = f"{item.body}\n\n{mark}{note}"
            cur = conn.execute(
                "INSERT INTO items (run_id, created_at, bucket, title, body,"
                " source_url, excerpt) VALUES (?,?,?,?,?,?,?)",
                (run_id, now.isoformat(), item.bucket, item.title, body,
                 item.source_url, item.excerpt))
            conn.commit()
            item_id = cur.lastrowid
            life.record(conn, "item_found", item_id=item_id,
                        detail=item.bucket, now=now)
            if draft_ok:
                life.record(conn, "draft_written", item_id=item_id,
                            engine=compose_engine, cost_usd=draft_cost, now=now)
        except Exception as exc:  # noqa: BLE001
            _end_run(conn, run_id, False, cost=spent,
                     error=f"{item.title}: {exc}"[:400], now=now)
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
            job_engine = engines.get_engine(conn)
            if job_engine == "auto":
                # The job prompt names no model, so the ladder only picks the
                # CLI here; a job never asks for Grok over Flash by name.
                step, _fallen_from = cascade.choose(conn, now)
                job_engine = step.engine
            spent += jobs.run_next(conn, runner_module, workspace,
                                   engine=job_engine)
    except Exception as exc:  # noqa: BLE001
        _end_run(conn, run_id, False, cost=spent, error=f"job: {exc}"[:400], now=now)
        return
    _end_run(conn, run_id, True, cost=spent, now=now)
