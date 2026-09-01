"""One cycle: quota, mail, drafts. launchd calls this module."""
import datetime as dt
import sqlite3

from nightshift import (cascade, drafts, engines, jobs, life, projects,
                        quota, runs)


def run_once(conn: sqlite3.Connection, *, runner_module, mail_module,
             now: dt.datetime, ceiling_usd: float, workspace,
             mail_engine: str = "claude") -> None:
    # The window starts where the last good cycle started, so the system never
    # pays twice to classify the same mail, and a gap of days widens it alone.
    # `kind = 'mail'` matters: the tick also writes a run, once an hour, and
    # without this the window would start at the last tick and hide every
    # message older than one hour. Read before runs.start below: the row that
    # call inserts has no `ok` yet, so it would not match anyway, but the
    # query means "the last run before this one" and reads clearest here.
    previous = conn.execute(
        "SELECT started_at FROM runs WHERE ok = 1 AND kind = 'mail'"
        " ORDER BY id DESC LIMIT 1").fetchone()
    since = previous["started_at"] if previous else None

    # The run opens before anything that can fail: projects.sync reads the
    # disk and fire_templates writes to the jobs table, and a crash in
    # either used to happen before any row existed. The page then read the
    # last good run, from yesterday, as today's state: a quiet day that hid
    # a crash.
    run_id = runs.start(conn, now, "mail")

    # Before the quota check, and free: firing a template writes a row, it
    # spends nothing, and a due job must sit in the queue by the time the
    # cycle below looks at it.
    # Reading directories costs milliseconds and no tokens, so a new project
    # appears on its own. A nightly task for this would add a call to the
    # budget and a plist to maintain, for the same result.
    try:
        try:
            projects.sync(conn)
        except OSError as exc:  # a missing folder must not stop the mail
            life.record(conn, "projects_skipped", detail=str(exc)[:200], now=now)
        jobs.fire_templates(conn, now)
    except Exception as exc:  # noqa: BLE001
        runs.end(conn, run_id, False, error=f"projects/templates: {exc}"[:400],
                 now=now)
        return

    decision = quota.may_run(conn, now=now, ceiling_usd=ceiling_usd)
    if not decision.allowed:
        runs.end(conn, run_id, False, error=decision.reason, now=now)
        return

    # Rule 2 of the cascade design: only Claude holds the Gmail connector, so
    # the fetch never walks the ladder. When Claude has no room this is not a
    # failure, it is the budget running its course, so the cycle ends clean
    # and waits for the next day.
    room_ok, room_reason = cascade.has_room(conn, cascade.LADDER[0], now)
    if not room_ok:
        runs.end(conn, run_id, True, error=room_reason, now=now)
        return

    # There is no separate health check. It costs as much as the work that it
    # would protect, so the triage itself reports an authentication failure.
    result = mail_module.triage(runner_module, cwd=workspace, since=since)
    spent = result.cost_usd
    # The triage is the most expensive call of the cycle, and only Claude
    # makes it. It used to ride in `cycle_ran`, which names no engine, and
    # `cascade.spent_by` reads only the events that name one. So the ladder
    # judged Claude on the drafts alone. A triage that failed spent too.
    life.record(conn, "triage_ran", engine="claude", cost_usd=spent,
                detail=result.error, now=now)
    if result.error:
        runs.end(conn, run_id, False, cost=spent, error=result.error[:400], now=now)
        return

    # Which engine composes the reply. A fixed name runs exactly as before;
    # "auto" walks the ladder once for the whole cycle, so every item in it
    # gets the same choice and the page can name the one engine that worked.
    compose_engine, compose_model = mail_engine, None
    if mail_engine == "auto":
        step, _fallen_from = cascade.choose(conn, now)
        compose_engine, compose_model = step.engine, step.model

    def keep(item, body) -> int:
        """Write one item and give back its id. Each item commits on its
        own: one exception in the middle used to throw away every draft
        already written and every dollar already spent."""
        cur = conn.execute(
            "INSERT INTO items (run_id, created_at, bucket, title, body,"
            " source_url, excerpt) VALUES (?,?,?,?,?,?,?)",
            (run_id, now.isoformat(), item.bucket, item.title, body,
             item.source_url, item.excerpt))
        conn.commit()
        return cur.lastrowid

    stopped_by_budget = 0
    for item in result.items:
        # The triage reads a 24 hour window and the scheduler runs twice a
        # day, so every message comes back at least once. A message that
        # already has an item keeps its old card and gets no second draft.
        if conn.execute("SELECT 1 FROM items WHERE source_url = ? LIMIT 1",
                        (item.source_url,)).fetchone():
            continue

        try:
            # The ceiling is read here too, not only before the cycle. A
            # large inbox on the first run would otherwise spend a whole week
            # of quota before anything stopped it.
            no_room = decision.spent_usd + spent >= ceiling_usd
            if item.bucket == "needs_you" and no_room:
                stopped_by_budget += 1
                item_id = keep(item, item.body)
            elif item.bucket == "needs_you":
                answer, item_id = drafts.for_item(
                    conn, mail_module, runner_module, item,
                    engine=compose_engine, model=compose_model, cwd=workspace,
                    save=lambda body: keep(item, body), now=now)
                spent += answer.cost_usd
            else:
                item_id = keep(item, item.body)
            life.record(conn, "item_found", item_id=item_id,
                        detail=item.bucket, now=now)
        except Exception as exc:  # noqa: BLE001
            runs.end(conn, run_id, False, cost=spent,
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
             "wait for the next week, then run the cycle again.", "/semana"))
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
        runs.end(conn, run_id, False, cost=spent, error=f"job: {exc}"[:400], now=now)
        return
    runs.end(conn, run_id, True, cost=spent, now=now)
