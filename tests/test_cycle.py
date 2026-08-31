import datetime as dt

from nightshift import cycle, db, life

NOW = dt.datetime(2026, 8, 26, 3, 0)


class Stub:
    def __init__(self, items=None, error=None, cost=0.5):
        self.items, self.error, self.cost = items or [], error, cost
        self.ran, self.drafted = False, 0

    def triage(self, *a, **kw):
        from nightshift.mail import TriageResult
        self.ran = True
        self.since = kw.get("since")
        return TriageResult(self.items, self.cost, self.error)

    def write_draft(self, *a, **kw):
        from nightshift.mail import DraftResult
        self.drafted += 1
        return DraftResult(0.8, True, "I asked for the three layouts.")

    def compose(self, *a, **kw):
        from nightshift.engines import EngineRun
        self.composed = getattr(self, "composed", 0) + 1
        return EngineRun(True, text="Sure, Friday works for me.",
                         cost_usd=0.1)

    def save_draft(self, *a, **kw):
        from nightshift.mail import DraftResult
        self.saved = getattr(self, "saved", 0) + 1
        return DraftResult(0.2, True, "Saved the reply Ollama wrote.")


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


def test_the_cycle_runs_one_job_and_adds_its_cost(tmp_path, monkeypatch):
    """The job queue had no caller. This proves the cycle now spends on it
    and that the spend lands where the governor reads it."""
    conn = db.connect(tmp_path / "s.db")
    stub = Stub(cost=0.5)

    def fake_run_next(conn_arg, runner_module, workspace, engine=None):
        assert conn_arg is conn
        assert runner_module is stub
        assert engine == "claude"  # nothing stored yet, so the default holds
        return 0.4

    monkeypatch.setattr(cycle.jobs, "run_next", fake_run_next)
    cycle.run_once(conn, runner_module=stub, mail_module=stub,
                   now=NOW, ceiling_usd=5.0, workspace=tmp_path)
    assert _last_run(conn)["cost_usd"] == 0.9  # 0.5 triage + 0.4 job


def test_work_already_done_survives_an_item_that_explodes(tmp_path):
    """The inserts used to commit once, after the loop. One exception threw
    away every draft already written and every cost already spent."""
    from nightshift.mail import DraftResult, Item
    conn = db.connect(tmp_path / "s.db")

    class Exploding(Stub):
        def write_draft(self, *a, **kw):
            self.drafted += 1
            if self.drafted == 2:
                raise RuntimeError("the tool answered nonsense")
            return DraftResult(0.8, True, "one line")

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


def test_the_budget_stops_the_drafts_inside_one_cycle(tmp_path):
    """The governor checked the ceiling only before the cycle started. A big
    inbox on the first run could spend a whole week of quota in one go."""
    from nightshift.mail import Item
    conn = db.connect(tmp_path / "s.db")
    items = [Item("needs_you", f"m{i}", "w", f"https://x/{i}") for i in range(5)]
    stub = Stub(items=items, cost=0.5)  # each draft costs 0.8
    cycle.run_once(conn, runner_module=stub, mail_module=stub,
                   now=NOW, ceiling_usd=2.0, workspace=tmp_path)
    # 0.5 triage, then 0.8 and 0.8. The third draft never starts.
    assert stub.drafted == 2


def test_the_drafts_that_the_budget_stopped_are_visible(tmp_path):
    """A draft that never got written must not look like a message that
    needed no answer."""
    from nightshift.mail import Item
    conn = db.connect(tmp_path / "s.db")
    items = [Item("needs_you", f"m{i}", "w", f"https://x/{i}") for i in range(5)]
    cycle.run_once(conn, runner_module=Stub(items=items, cost=0.5),
                   mail_module=Stub(items=items, cost=0.5),
                   now=NOW, ceiling_usd=2.0, workspace=tmp_path)
    titles = [r["title"] for r in conn.execute("SELECT title FROM items")]
    assert any("budget" in t.lower() for t in titles), titles


def test_the_item_keeps_the_trace_of_its_draft(tmp_path):
    """The cycle threw away what the agent said it wrote, so nothing on the
    page could show that a draft came out empty."""
    from nightshift.mail import DraftResult, Item
    conn = db.connect(tmp_path / "s.db")

    class WithNote(Stub):
        def write_draft(self, *a, **kw):
            self.drafted += 1
            return DraftResult(0.8, False, "The agent reported an empty draft.")

    stub = WithNote(items=[Item("needs_you", "Shannon", "3 layouts.",
                                "https://x/1")], cost=0.5)
    cycle.run_once(conn, runner_module=stub, mail_module=stub,
                   now=NOW, ceiling_usd=45.0, workspace=tmp_path)
    body = conn.execute("SELECT body FROM items").fetchone()[0]
    assert "3 layouts." in body        # the reason stays
    assert "empty draft" in body.lower()  # and the trouble is visible


def test_the_excerpt_reaches_the_items_table(tmp_path):
    """mailstore reads the excerpt straight from this column, so the cycle
    must write it, not just the reason."""
    from nightshift.mail import Item
    conn = db.connect(tmp_path / "s.db")
    stub = Stub(items=[Item("no_action", "AWS bill", "It is a receipt.",
                            "https://x/1",
                            excerpt="Your invoice for August is attached.")])
    cycle.run_once(conn, runner_module=stub, mail_module=stub,
                   now=NOW, ceiling_usd=5.0, workspace=tmp_path)
    excerpt = conn.execute("SELECT excerpt FROM items").fetchone()[0]
    assert excerpt == "Your invoice for August is attached."


def test_a_message_already_seen_gets_no_second_draft(tmp_path):
    """The triage reads a 24 hour window and the scheduler runs twice a day,
    so every message arrives at least twice. Without this the user collects
    duplicate drafts for the same mail."""
    from nightshift.mail import Item
    conn = db.connect(tmp_path / "s.db")
    same = [Item("needs_you", "Shannon: deck", "3 layouts.", "https://x/1")]

    first = Stub(items=same, cost=0.5)
    cycle.run_once(conn, runner_module=first, mail_module=first,
                   now=NOW, ceiling_usd=45.0, workspace=tmp_path)
    second = Stub(items=same, cost=0.5)
    cycle.run_once(conn, runner_module=second, mail_module=second,
                   now=NOW, ceiling_usd=45.0, workspace=tmp_path)

    assert first.drafted == 1
    assert second.drafted == 0  # the same message, no second draft
    assert conn.execute("SELECT count(*) FROM items").fetchone()[0] == 1


def test_the_cycle_asks_only_for_mail_since_the_last_good_run(tmp_path):
    """Every cycle used to pay to classify the same day of mail again."""
    conn = db.connect(tmp_path / "s.db")
    conn.execute("INSERT INTO runs (started_at, kind, ok, cost_usd)"
                 " VALUES ('2026-08-26T01:00:00','mail',1,0.4)")
    conn.commit()
    stub = Stub()
    cycle.run_once(conn, runner_module=stub, mail_module=stub,
                   now=NOW, ceiling_usd=45.0, workspace=tmp_path)
    assert stub.since == "2026-08-26T01:00:00"


def test_the_first_cycle_of_all_has_no_since(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    stub = Stub()
    cycle.run_once(conn, runner_module=stub, mail_module=stub,
                   now=NOW, ceiling_usd=45.0, workspace=tmp_path)
    assert stub.since is None


# --- which engine composes the reply ----------------------------------

def test_claude_as_mail_engine_writes_and_never_composes(tmp_path):
    from nightshift.mail import Item
    conn = db.connect(tmp_path / "s.db")
    stub = Stub(items=[Item("needs_you", "Shannon: deck", "3 layouts.",
                            "https://x/1")])
    cycle.run_once(conn, runner_module=stub, mail_module=stub,
                   now=NOW, ceiling_usd=5.0, workspace=tmp_path,
                   mail_engine="claude")
    assert stub.drafted == 1
    assert getattr(stub, "composed", 0) == 0
    assert getattr(stub, "saved", 0) == 0


def test_ollama_as_mail_engine_composes_then_saves_and_sums_the_cost(
        tmp_path):
    from nightshift.mail import Item
    conn = db.connect(tmp_path / "s.db")
    stub = Stub(items=[Item("needs_you", "Shannon: deck", "3 layouts.",
                            "https://x/1")], cost=0.5)
    cycle.run_once(conn, runner_module=stub, mail_module=stub,
                   now=NOW, ceiling_usd=5.0, workspace=tmp_path,
                   mail_engine="ollama")
    assert stub.drafted == 0
    assert stub.composed == 1
    assert stub.saved == 1
    # 0.5 triage + 0.1 compose + 0.2 save
    assert _last_run(conn)["cost_usd"] == 0.8


def test_a_failed_compose_skips_save_draft_and_the_item_says_so(tmp_path):
    from nightshift.engines import EngineRun
    from nightshift.mail import Item

    class ComposeFails(Stub):
        def compose(self, *a, **kw):
            self.composed = getattr(self, "composed", 0) + 1
            return EngineRun(False, error="dsh is not installed", cost_usd=0.1)

        def save_draft(self, *a, **kw):
            raise AssertionError("save_draft must not run after a failed"
                                 " compose")

    conn = db.connect(tmp_path / "s.db")
    stub = ComposeFails(items=[Item("needs_you", "Shannon: deck", "3 layouts.",
                                    "https://x/1")], cost=0.5)
    cycle.run_once(conn, runner_module=stub, mail_module=stub,
                   now=NOW, ceiling_usd=5.0, workspace=tmp_path,
                   mail_engine="ollama")
    assert stub.composed == 1
    body = conn.execute("SELECT body FROM items").fetchone()[0]
    assert "NO DRAFT" in body
    assert "dsh is not installed" in body


# --- the life of an item, seen from the cycle -------------------------

def test_a_closed_item_does_not_come_back_when_the_cycle_runs_again(
        tmp_path):
    """A closed item is still a message with a source_url. The next cycle
    must not read it as new mail and write a second draft for it."""
    from nightshift.mail import Item
    conn = db.connect(tmp_path / "s.db")
    same = [Item("needs_you", "Shannon: deck", "3 layouts.", "https://x/1")]

    first = Stub(items=same, cost=0.5)
    cycle.run_once(conn, runner_module=first, mail_module=first,
                   now=NOW, ceiling_usd=45.0, workspace=tmp_path)
    item_id = conn.execute("SELECT id FROM items").fetchone()[0]
    life.apply_verb(conn, item_id, "listo", now=NOW)

    second = Stub(items=same, cost=0.5)
    cycle.run_once(conn, runner_module=second, mail_module=second,
                   now=NOW + dt.timedelta(days=1), ceiling_usd=45.0,
                   workspace=tmp_path)

    assert second.drafted == 0
    assert conn.execute("SELECT count(*) FROM items").fetchone()[0] == 1
    assert conn.execute("SELECT state FROM items WHERE id=?",
                        (item_id,)).fetchone()[0] == "done"


def test_the_cycle_writes_item_found_and_cycle_ran_events(tmp_path):
    from nightshift.mail import Item
    conn = db.connect(tmp_path / "s.db")
    stub = Stub(items=[Item("needs_you", "Shannon: deck", "3 layouts.",
                            "https://x/1"),
                       Item("no_action", "AWS bill", "receipt.",
                            "https://x/2")], cost=0.5)
    cycle.run_once(conn, runner_module=stub, mail_module=stub,
                   now=NOW, ceiling_usd=5.0, workspace=tmp_path)

    found = conn.execute(
        "SELECT count(*) FROM events WHERE kind='item_found'").fetchone()[0]
    assert found == 2

    ran = conn.execute(
        "SELECT * FROM events WHERE kind='cycle_ran'").fetchall()
    assert len(ran) == 1
    assert ran[0]["cost_usd"] == 1.3  # 0.5 triage + 0.8 draft, as above


def test_a_cycle_that_fails_still_writes_one_cycle_ran_event(tmp_path):
    """`cycle_ran` mirrors the runs table on every exit, not only a clean
    one: the weekly board must see a stopped cycle too."""
    conn = db.connect(tmp_path / "s.db")
    stub = Stub(error="401 OAuth access token has expired")
    cycle.run_once(conn, runner_module=stub, mail_module=stub,
                   now=NOW, ceiling_usd=5.0, workspace=tmp_path)
    ran = conn.execute(
        "SELECT * FROM events WHERE kind='cycle_ran'").fetchall()
    assert len(ran) == 1
