"""The cheap half of the work: what costs nothing until there is something.

Firing a template is SQL. Running one queued job costs only when a job waits.
The mail cycle is the expensive half and it stays on its own schedule, because
it goes to Gmail even when it finds nothing.
"""
import datetime as dt
import sqlite3

from nightshift import cascade, engines, jobs, life, projects, quota, runs


def run(conn: sqlite3.Connection, *, runner_module, workspace,
        now: dt.datetime | None = None, ceiling_usd: float,
        max_jobs: int = 1) -> dict:
    now = now or dt.datetime.now()

    # The tick spends: it runs queued jobs. `quota.spent_this_week` reads the
    # `runs` table alone, so a tick that opened no run was invisible to the
    # ceiling, and the plist fires it every hour.
    run_id = runs.start(conn, now, "tick")
    jobs_run = 0
    cost_usd = 0.0
    fired = skipped = 0
    reason = ""

    try:
        # Free: reading directories costs no tokens, so the dropdown stays
        # fresh on every tick instead of waiting for the next mail cycle.
        projects.sync(conn)

        fired_result = jobs.fire_templates(conn, now)
        fired, skipped = fired_result["made"], fired_result["skipped"]

        if jobs.next_queued(conn) is not None:
            decision = quota.may_run(conn, now, ceiling_usd)
            if not decision.allowed:
                reason = decision.reason
            else:
                job_engine = engines.get_engine(conn)
                if job_engine == "auto":
                    # A job prompt names no model, so the ladder only picks
                    # the CLI here, the same way the cycle resolves it.
                    step, _fallen_from = cascade.choose(conn, now)
                    job_engine = step.engine
                while jobs_run < max_jobs and jobs.next_queued(conn) is not None:
                    if decision.spent_usd + cost_usd >= ceiling_usd:
                        reason = decision.reason
                        break
                    cost_usd += jobs.run_next(conn, runner_module, workspace,
                                              engine=job_engine)
                    jobs_run += 1
    except Exception as exc:  # noqa: BLE001
        # Every exit path closes the run, this one too. A row that never ends
        # keeps a cost of zero, and the ceiling would never see what the tick
        # already spent before it broke.
        runs.end(conn, run_id, False, cost=cost_usd, error=str(exc)[:400],
                 now=now)
        raise

    runs.end(conn, run_id, True, cost=cost_usd, now=now)

    # A tick that found nothing writes nothing: an empty table is easier to
    # read than one full of rows that say "nothing happened". The cost rides
    # in the run above, which is what the governor reads.
    if fired or skipped or jobs_run:
        life.record(
            conn, "tick_ran",
            detail=f"fired={fired} skipped={skipped} jobs_run={jobs_run}",
            now=now)

    return {"fired": fired, "skipped": skipped, "jobs_run": jobs_run,
            "cost_usd": cost_usd, "reason": reason}
