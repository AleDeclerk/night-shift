import datetime as dt

from nightshift import cycle, db

NOW = dt.datetime(2026, 8, 26, 3, 0)


class Stub:
    def __init__(self, items=None, error=None, cost=0.5):
        self.items, self.error, self.cost = items or [], error, cost
        self.ran, self.drafted = False, 0

    def triage(self, *a, **kw):
        from nightshift.mail import TriageResult
        self.ran = True
        return TriageResult(self.items, self.cost, self.error)

    def write_draft(self, *a, **kw):
        from nightshift.runner import RunResult
        self.drafted += 1
        return RunResult(True, text="one line", cost_usd=0.8)


def _last_run(conn):
    return conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()


def test_an_authentication_failure_lands_on_the_run_record(tmp_path):
    """The page reads this row, so the cause must reach the database."""
    conn = db.connect(tmp_path / "s.db")
    stub = Stub(error="401 OAuth access token has expired")
    cycle.run_once(conn, runner_module=stub, mail_module=stub,
                   now=NOW, ceiling_usd=5.0, workspace=tmp_path)
    row = _last_run(conn)
    assert row["ok"] == 0 and "401" in row["error"]
    assert stub.drafted == 0


def test_the_run_records_what_it_spent(tmp_path):
    """Without this the governor counts zero and it never stops the system."""
    from nightshift.mail import Item
    conn = db.connect(tmp_path / "s.db")
    stub = Stub(items=[Item("needs_you", "t", "w", "https://x/1")], cost=0.5)
    cycle.run_once(conn, runner_module=stub, mail_module=stub,
                   now=NOW, ceiling_usd=5.0, workspace=tmp_path)
    assert _last_run(conn)["cost_usd"] == 1.3  # 0.5 triage + 0.8 draft


def test_a_full_ceiling_stops_the_cycle(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    conn.execute("INSERT INTO runs (started_at, kind, ok, cost_usd)"
                 " VALUES (?, 'mail', 1, 9.0)", (NOW.isoformat(),))
    conn.commit()
    stub = Stub()
    cycle.run_once(conn, runner_module=stub, mail_module=stub,
                   now=NOW, ceiling_usd=5.0, workspace=tmp_path)
    assert stub.ran is False


def test_an_item_that_needs_you_gets_a_draft(tmp_path):
    from nightshift.mail import Item
    conn = db.connect(tmp_path / "s.db")
    stub = Stub(items=[Item("needs_you", "Shannon: deck", "3 layouts.",
                            "https://x/1"),
                       Item("no_action", "AWS bill", "receipt.", "https://x/2")])
    cycle.run_once(conn, runner_module=stub, mail_module=stub,
                   now=NOW, ceiling_usd=5.0, workspace=tmp_path)
    assert stub.drafted == 1  # only the needs_you item
    assert conn.execute("SELECT count(*) FROM items").fetchone()[0] == 2


def test_work_already_done_survives_an_item_that_explodes(tmp_path):
    """The inserts used to commit once, after the loop. One exception threw
    away every draft already written and every cost already spent."""
    from nightshift.mail import Item
    from nightshift.runner import RunResult
    conn = db.connect(tmp_path / "s.db")

    class Exploding(Stub):
        def write_draft(self, *a, **kw):
            self.drafted += 1
            if self.drafted == 2:
                raise RuntimeError("the tool answered nonsense")
            return RunResult(True, text="one line", cost_usd=0.8)

    stub = Exploding(items=[Item("needs_you", "first", "w", "https://x/1"),
                            Item("needs_you", "second", "w", "https://x/2")],
                     cost=0.5)
    cycle.run_once(conn, runner_module=stub, mail_module=stub,
                   now=NOW, ceiling_usd=45.0, workspace=tmp_path)

    assert conn.execute("SELECT count(*) FROM items").fetchone()[0] == 1
    row = _last_run(conn)
    assert row["ok"] == 0
    assert "nonsense" in row["error"]
    assert row["cost_usd"] == 1.3  # the triage and the draft that did work
