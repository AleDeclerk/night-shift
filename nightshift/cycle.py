"""One cycle: quota, mail, drafts. launchd calls this module."""
import datetime as dt
import sqlite3

from nightshift import jobs, quota


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
    run_id = _start_run(conn, now, "mail")

    decision = quota.may_run(conn, now=now, ceiling_usd=ceiling_usd)
    if not decision.allowed:
        _end_run(conn, run_id, False, error=decision.reason)
        return

    # There is no separate health check. It costs as much as the work that it
    # would protect, so the triage itself reports an authentication failure.
    result = mail_module.triage(runner_module, cwd=workspace)
    spent = result.cost_usd
    if result.error:
        _end_run(conn, run_id, False, cost=spent, error=result.error[:400])
        return

    # Each item commits on its own. One exception in the middle used to throw
    # away every draft already written and every dollar already spent.
    for item in result.items:
        try:
            if item.bucket == "needs_you":
                draft = mail_module.write_draft(runner_module, item,
                                                cwd=workspace)
                spent += draft.cost_usd
            conn.execute(
                "INSERT INTO items (run_id, created_at, bucket, title, body,"
                " source_url) VALUES (?,?,?,?,?,?)",
                (run_id, now.isoformat(), item.bucket, item.title, item.body,
                 item.source_url))
            conn.commit()
        except Exception as exc:  # noqa: BLE001
            _end_run(conn, run_id, False, cost=spent,
                     error=f"{item.title}: {exc}"[:400])
            return

    # One job per cycle. Wrapped like the item loop above: a job that
    # explodes must not lose the cost already spent on the mail triage.
    try:
        spent += jobs.run_next(conn, runner_module, workspace)
    except Exception as exc:  # noqa: BLE001
        _end_run(conn, run_id, False, cost=spent, error=f"job: {exc}"[:400])
        return
    _end_run(conn, run_id, True, cost=spent)
