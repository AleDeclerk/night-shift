"""The cheap half of the work: what costs nothing until there is something.

Firing a template is SQL. Running one queued job costs only when a job waits.
The mail cycle is the expensive half and it stays on its own schedule, because
it goes to Gmail even when it finds nothing.
"""
import datetime as dt
import sqlite3

from nightshift import cascade, engines, jobs, life, projects, quota


def run(conn: sqlite3.Connection, *, runner_module, workspace,
        now: dt.datetime | None = None, ceiling_usd: float,
        max_jobs: int = 1) -> dict:
    now = now or dt.datetime.now()

    # Free: reading directories costs no tokens, so the dropdown stays fresh
    # on every tick instead of waiting for the next mail cycle.
    projects.sync(conn)

    fired_result = jobs.fire_templates(conn, now)
    fired, skipped = fired_result["made"], fired_result["skipped"]

    jobs_run = 0
    cost_usd = 0.0
    reason = ""

    if jobs.next_queued(conn) is not None:
        decision = quota.may_run(conn, now, ceiling_usd)
        if not decision.allowed:
            reason = decision.reason
        else:
            job_engine = engines.get_engine(conn)
            if job_engine == "auto":
                # A job prompt names no model, so the ladder only picks the
                # CLI here, the same way the cycle resolves it.
                step, _fallen_from = cascade.choose(conn, now)
                job_engine = step.engine
            while jobs_run < max_jobs and jobs.next_queued(conn) is not None:
                if decision.spent_usd + cost_usd >= ceiling_usd:
                    reason = decision.reason
                    break
                cost_usd += jobs.run_next(conn, runner_module, workspace,
                                          engine=job_engine)
                jobs_run += 1

    # A tick that found nothing writes nothing: an empty table is easier to
    # read than one full of rows that say "nothing happened".
    if fired or skipped or jobs_run:
        life.record(
            conn, "tick_ran", cost_usd=cost_usd,
            detail=f"fired={fired} skipped={skipped} jobs_run={jobs_run}",
            now=now)

    return {"fired": fired, "skipped": skipped, "jobs_run": jobs_run,
            "cost_usd": cost_usd, "reason": reason}
