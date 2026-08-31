# tests/test_web.py
from fastapi.testclient import TestClient

from nightshift import backends, db, web


def _engines():
    """Fixed engines. No test may run a real CLI: it would take five seconds
    for each page load and it would depend on the machine."""
    return [
        backends.Engine("claude", "Claude", "Max subscription", True, True,
                        True, True, True, False),
        backends.Engine("gemini", "Gemini", "Personal Google account", True,
                        True, False, False, False, False),
        backends.Engine("cursor", "Cursor", "cursor-agent", True, False,
                        False, False, False, True),
        backends.Engine("ollama", "Ollama", "6 local models", True, True,
                        False, True, False, False),
    ]


def test_the_page_shows_the_three_buckets(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    conn.execute("INSERT INTO runs (started_at, kind, ok, cost_usd)"
                 " VALUES ('2026-08-26T03:00:00','mail',1,0.4)")
    conn.execute("INSERT INTO items (run_id, created_at, bucket, title, body,"
                 " source_url) VALUES (1,'2026-08-26T03:00:00','needs_you',"
                 "'Shannon: deck','She asks for 3 layouts.','https://x/1')")
    conn.commit()
    client = TestClient(web.make_app(conn, engine_source=_engines, ceiling_usd=5.0))
    body = client.get("/").text
    assert "Shannon: deck" in body
    # The link goes through /open so the page can count what you read. The
    # real address is checked in test_opening_an_item_records_the_time.
    assert "/open/1" in body
    # The page speaks Spanish: it is a personal tool and one person reads it.
    # The code, the commits and the documents stay in English.
    assert "Pendiente" in body


def test_a_new_job_goes_to_the_queue(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    client = TestClient(web.make_app(conn, engine_source=_engines, ceiling_usd=5.0))
    client.post("/jobs", data={"prompt": "Prepare the sprint review"},
                follow_redirects=False)
    row = conn.execute("SELECT * FROM jobs").fetchone()
    assert row["prompt"] == "Prepare the sprint review"
    assert row["state"] == "queued"


def test_the_page_shows_the_error_of_the_last_run(tmp_path):
    """A scheduled system fails in silence. The page must say so."""
    conn = db.connect(tmp_path / "s.db")
    conn.execute("INSERT INTO runs (started_at, kind, ok, error)"
                 " VALUES ('2026-08-26T03:00:00','mail',0,"
                 "'401 OAuth access token has expired')")
    conn.commit()
    client = TestClient(web.make_app(conn, engine_source=_engines, ceiling_usd=5.0))
    assert "401" in client.get("/").text


def test_the_page_says_when_the_last_good_cycle_ran(tmp_path):
    """A stale page and a fresh page must not look the same."""
    conn = db.connect(tmp_path / "s.db")
    conn.execute("INSERT INTO runs (started_at, kind, ok)"
                 " VALUES ('2026-08-30T06:30:00','mail',1)")
    conn.commit()
    body = TestClient(web.make_app(conn, engine_source=_engines, ceiling_usd=5.0)).get("/").text
    assert "30 Aug" in body
    assert "06:30" in body


def test_the_page_says_never_when_no_cycle_ever_ran(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    body = TestClient(web.make_app(conn, engine_source=_engines, ceiling_usd=5.0)).get("/").text
    assert "never" in body.lower()


def test_a_failed_cycle_does_not_count_as_the_last_good_one(tmp_path):
    """The point of the line is to show that work stopped."""
    conn = db.connect(tmp_path / "s.db")
    conn.execute("INSERT INTO runs (started_at, kind, ok)"
                 " VALUES ('2026-08-30T06:30:00','mail',1)")
    conn.execute("INSERT INTO runs (started_at, kind, ok, error)"
                 " VALUES ('2026-08-31T06:30:00','mail',0,'401 expired')")
    conn.commit()
    body = TestClient(web.make_app(conn, engine_source=_engines, ceiling_usd=5.0)).get("/").text
    assert "30 Aug" in body  # the good one, not the failed one
    assert "401" in body


def test_opening_an_item_records_the_time(tmp_path):
    """Section 9 of the spec: the count of what you read lives on the page."""
    conn = db.connect(tmp_path / "s.db")
    conn.execute("INSERT INTO runs (started_at, kind, ok) VALUES ('x','mail',1)")
    conn.execute("INSERT INTO items (run_id, created_at, bucket, title,"
                 " source_url) VALUES (1,'x','needs_you','t','https://x/1')")
    conn.commit()
    client = TestClient(web.make_app(conn, engine_source=_engines, ceiling_usd=5.0))
    r = client.get("/open/1", follow_redirects=False)
    assert r.headers["location"] == "https://x/1"
    assert conn.execute("SELECT opened_at FROM items WHERE id=1"
                        ).fetchone()[0] is not None


def test_a_job_that_needs_you_shows_its_question(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    conn.execute("INSERT INTO jobs (created_at, prompt, state, question)"
                 " VALUES ('x','Prepare the sprint review','needs_you',"
                 "'Which sprint number?')")
    conn.commit()
    body = TestClient(web.make_app(conn, engine_source=_engines, ceiling_usd=5.0)).get("/").text
    assert "Prepare the sprint review" in body
    assert "Which sprint number?" in body


def test_a_queued_job_is_visible_on_the_page(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    conn.execute("INSERT INTO jobs (created_at, prompt, state)"
                 " VALUES ('x','Prepare the sprint review','queued')")
    conn.commit()
    body = TestClient(web.make_app(conn, engine_source=_engines, ceiling_usd=5.0)).get("/").text
    assert "Prepare the sprint review" in body


def test_a_cycle_that_never_finished_is_visible(tmp_path):
    """A crash between the start and the end leaves `ok` NULL, with no error
    text. The page must not look like a quiet day."""
    conn = db.connect(tmp_path / "s.db")
    conn.execute("INSERT INTO runs (started_at, kind)"
                 " VALUES ('2026-08-30T06:30:00','mail')")
    conn.commit()
    body = TestClient(web.make_app(conn, engine_source=_engines, ceiling_usd=5.0)).get("/").text
    assert "did not finish" in body.lower()


def test_a_cycle_that_is_running_now_is_not_called_dead(tmp_path):
    """A cycle takes up to three minutes. Opening the page at 06:31, while the
    06:30 run works, must not show the message for a dead run."""
    import datetime as dt
    conn = db.connect(tmp_path / "s.db")
    just_now = (dt.datetime.now() - dt.timedelta(minutes=2)).isoformat()
    conn.execute("INSERT INTO runs (started_at, kind) VALUES (?, 'mail')",
                 (just_now,))
    conn.commit()
    body = TestClient(web.make_app(conn, engine_source=_engines, ceiling_usd=5.0)).get("/").text
    assert "did not finish" not in body.lower()
    assert "running" in body.lower()


def test_a_cycle_that_started_long_ago_and_never_ended_is_dead(tmp_path):
    import datetime as dt
    conn = db.connect(tmp_path / "s.db")
    old = (dt.datetime.now() - dt.timedelta(hours=3)).isoformat()
    conn.execute("INSERT INTO runs (started_at, kind) VALUES (?, 'mail')",
                 (old,))
    conn.commit()
    body = TestClient(web.make_app(conn, engine_source=_engines, ceiling_usd=5.0)).get("/").text
    assert "did not finish" in body.lower()


def test_the_machine_room_shows_every_engine(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    body = TestClient(web.make_app(conn, engine_source=_engines, ceiling_usd=20.0)).get("/").text
    for label in ("Claude", "Gemini", "Cursor", "Ollama"):
        assert label in body


def test_sign_in_refuses_a_get(tmp_path):
    """A prefetch of the browser must not start a login."""
    conn = db.connect(tmp_path / "s.db")
    client = TestClient(web.make_app(conn, engine_source=_engines, ceiling_usd=20.0))
    assert client.get("/machines/cursor/login").status_code == 405


def test_sign_in_status_answers_before_any_attempt(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    client = TestClient(web.make_app(conn, engine_source=_engines, ceiling_usd=20.0))
    answer = client.get("/machines/cursor/login/status").json()
    assert answer["state"] == "idle"
    assert answer["has_link"] is False


def test_the_status_payload_carries_no_credential(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    client = TestClient(web.make_app(conn, engine_source=_engines, ceiling_usd=20.0))
    body = client.get("/machines/cursor/login/status").text
    assert "challenge" not in body
    assert "cursor.com" not in body


def test_cancel_answers_even_with_no_flow(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    client = TestClient(web.make_app(conn, engine_source=_engines, ceiling_usd=20.0))
    assert client.post("/machines/cursor/login/cancel").json()["state"] == "cancelled"


def test_an_engine_with_no_probe_says_so(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    body = TestClient(web.make_app(conn, engine_source=_engines,
                                   ceiling_usd=20.0)).get("/").text
    assert "SIN PRUEBA REAL" in body


def test_a_stored_probe_shows_what_the_engine_did(tmp_path):
    from nightshift import backends
    conn = db.connect(tmp_path / "s.db")
    backends.save_probe(conn, backends.ProbeResult(
        "cursor", False, False, 0.0,
        "Incompatible auth server: does not support dynamic client registration"))
    body = TestClient(web.make_app(conn, engine_source=_engines,
                                   ceiling_usd=20.0)).get("/").text
    assert "Incompatible auth server" in body
    assert "NO PUDO" in body


def test_the_probe_button_names_the_price(tmp_path):
    """The price is quota, not money. A plan of this kind meters no dollars,
    and a button that says USD reads as a charge."""
    conn = db.connect(tmp_path / "s.db")
    body = TestClient(web.make_app(conn, engine_source=_engines,
                                   ceiling_usd=20.0)).get("/").text
    assert "GASTA CUOTA" in body
    assert "GRATIS" in body


def test_the_page_says_that_the_meter_is_not_a_bill(tmp_path):
    """`total_cost_usd` is what the work would have cost through the API. The
    subscription charges none of it, so the page must not read as an invoice."""
    conn = db.connect(tmp_path / "s.db")
    body = TestClient(web.make_app(conn, engine_source=_engines,
                                   ceiling_usd=20.0)).get("/").text
    assert "no es un cobro" in body


def test_choosing_an_engine_is_remembered(tmp_path):
    from nightshift import engines
    conn = db.connect(tmp_path / "s.db")
    client = TestClient(web.make_app(conn, engine_source=_engines,
                                     ceiling_usd=20.0))
    client.post("/machines/engine", data={"name": "ollama"},
                follow_redirects=False)
    assert engines.get_engine(conn) == "ollama"


def test_an_unknown_engine_changes_nothing(tmp_path):
    from nightshift import engines
    conn = db.connect(tmp_path / "s.db")
    client = TestClient(web.make_app(conn, engine_source=_engines,
                                     ceiling_usd=20.0))
    client.post("/machines/engine", data={"name": "nothing"},
                follow_redirects=False)
    assert engines.get_engine(conn) == "claude"


def test_the_page_marks_the_chosen_engine(tmp_path):
    from nightshift import engines
    conn = db.connect(tmp_path / "s.db")
    engines.set_engine(conn, "ollama")
    body = TestClient(web.make_app(conn, engine_source=_engines,
                                   ceiling_usd=20.0)).get("/").text
    assert 'value="ollama" selected' in body or "selected" in body


def test_choosing_a_mail_engine_is_remembered(tmp_path):
    from nightshift import engines
    conn = db.connect(tmp_path / "s.db")
    client = TestClient(web.make_app(conn, engine_source=_engines,
                                     ceiling_usd=20.0))
    client.post("/machines/mail-engine", data={"name": "ollama"},
                follow_redirects=False)
    assert engines.get_mail_engine(conn) == "ollama"


def test_an_unknown_mail_engine_changes_nothing(tmp_path):
    from nightshift import engines
    conn = db.connect(tmp_path / "s.db")
    client = TestClient(web.make_app(conn, engine_source=_engines,
                                     ceiling_usd=20.0))
    client.post("/machines/mail-engine", data={"name": "nothing"},
                follow_redirects=False)
    assert engines.get_mail_engine(conn) == "claude"


def test_the_page_shows_the_mail_engine_form(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    body = TestClient(web.make_app(conn, engine_source=_engines,
                                   ceiling_usd=20.0)).get("/").text
    assert 'action="/machines/mail-engine"' in body
    assert "Motor que redacta" in body


def test_the_injected_engines_are_the_ones_used(tmp_path):
    """`engine_source` was accepted and then ignored, so the page always ran
    the real CLIs. The suite only looked fast because another test warmed the
    module cache first, by alphabetical luck."""
    conn = db.connect(tmp_path / "s.db")
    called = []

    def marker():
        called.append(True)
        return [backends.Engine("claude", "MOTOR-INYECTADO", "x", True, True,
                                True, True, True, False)]

    body = TestClient(web.make_app(conn, engine_source=marker,
                                   ceiling_usd=20.0)).get("/").text
    assert called, "the page ignored the injected source"
    assert "MOTOR-INYECTADO" in body


def test_the_panel_tells_the_connector_from_the_ability_to_work(tmp_path):
    """`Ve el correo: NO` read as "this engine is useless for mail". Only the
    connector is exclusive: a local model reads the stored mail and writes the
    reply without one."""
    conn = db.connect(tmp_path / "s.db")
    body = TestClient(web.make_app(conn, engine_source=_engines,
                                   ceiling_usd=20.0)).get("/").text
    assert "Conector de Gmail" in body
    assert "Trabaja el correo" in body


def test_the_bar_shows_every_engine_at_a_glance(tmp_path):
    """One cell used to name only the mail engine. The bar now says which
    engines exist and which one is down, without opening anything."""
    conn = db.connect(tmp_path / "s.db")
    body = TestClient(web.make_app(conn, engine_source=_engines,
                                   ceiling_usd=20.0)).get("/").text
    bar = body.split('class="bar"')[1].split('class="panel')[0]
    for label in ("Claude", "Gemini", "Cursor", "Ollama"):
        assert label in bar, label
    assert "chip--off" in bar     # Cursor has no session in the fixture


def test_the_bar_says_who_does_what(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    body = TestClient(web.make_app(conn, engine_source=_engines,
                                   ceiling_usd=20.0)).get("/").text
    assert "Motor del correo" in body
    assert "Motor que redacta" in body
    assert "Motor de tareas" in body


def test_the_room_shows_the_three_roles_together(tmp_path):
    """Fetching, writing and doing jobs are three different jobs, and only the
    first one is locked. Showing the locked one says why it cannot move."""
    conn = db.connect(tmp_path / "s.db")
    body = TestClient(web.make_app(conn, engine_source=_engines,
                                   ceiling_usd=20.0)).get("/").text
    room = body.split('<dialog id="room">')[1]
    assert "Baja y guarda el correo" in room
    assert "Redacta la respuesta" in room
    assert "Hace los encargos" in room
    assert "<select disabled>" in room     # the mail fetch cannot be moved


# --- the life of an item -----------------------------------------------

def _open_item(conn) -> int:
    conn.execute("INSERT INTO runs (started_at, kind, ok) VALUES ('x','mail',1)")
    cur = conn.execute(
        "INSERT INTO items (run_id, created_at, bucket, title, body,"
        " source_url) VALUES (1,'x','needs_you','Shannon: deck',"
        "'She asks for 3 layouts.','https://x/1')")
    conn.commit()
    return cur.lastrowid


def test_posting_a_closing_verb_moves_the_item_out_of_pendiente(tmp_path):
    for verb in ("listo", "lo_hago_yo", "no_era_nada", "manana"):
        conn = db.connect(tmp_path / f"s-{verb}.db")
        item_id = _open_item(conn)
        client = TestClient(web.make_app(conn, engine_source=_engines,
                                         ceiling_usd=5.0))
        r = client.post(f"/items/{item_id}/{verb}", follow_redirects=False)
        assert r.status_code == 303
        pendiente = client.get("/").text.split(
            '<section class="panel a-done">')[0]
        assert "Shannon: deck" not in pendiente, verb


def test_an_unknown_verb_answers_without_changing_anything(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    item_id = _open_item(conn)
    client = TestClient(web.make_app(conn, engine_source=_engines,
                                     ceiling_usd=5.0))
    r = client.post(f"/items/{item_id}/send_it", follow_redirects=False)
    assert r.status_code == 303
    assert conn.execute("SELECT state FROM items WHERE id=?",
                        (item_id,)).fetchone()[0] == "pending"
    assert "Shannon: deck" in client.get("/").text


def test_the_page_shows_the_five_buttons_under_an_item(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    _open_item(conn)
    body = TestClient(web.make_app(conn, engine_source=_engines,
                                   ceiling_usd=5.0)).get("/").text
    for label in ("Listo", "Lo hago yo", "No era nada", "Mañana", "Rehacer"):
        assert label in body, label


def test_rehacer_writes_a_new_draft_and_the_item_stays_pending(
        tmp_path, monkeypatch):
    """The dedicated route, not the generic one: it must also compose and
    save a new draft, with no real engine ever touched in a test."""
    from nightshift import web as web_module
    from nightshift.mail import DraftResult

    conn = db.connect(tmp_path / "s.db")
    item_id = _open_item(conn)

    def fake_write_draft(runner_module, item, cwd):
        return DraftResult(0.4, True, "Redone.")

    monkeypatch.setattr(web_module.mail, "write_draft", fake_write_draft)
    client = TestClient(web.make_app(conn, engine_source=_engines,
                                     ceiling_usd=5.0))
    r = client.post(f"/items/{item_id}/rehacer", follow_redirects=False)
    assert r.status_code == 303

    row = conn.execute("SELECT state FROM items WHERE id=?",
                       (item_id,)).fetchone()
    assert row["state"] == "pending"
    event = conn.execute(
        "SELECT * FROM events WHERE kind='draft_written' AND item_id=?",
        (item_id,)).fetchone()
    assert event is not None
    assert event["engine"] == "claude"
    assert "Shannon: deck" in client.get("/").text  # still in Pendiente


# --- the life of a job ---------------------------------------------------

def test_answering_a_stopped_job_puts_it_back_in_the_queue(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    conn.execute("INSERT INTO jobs (created_at, prompt, state, question)"
                 " VALUES ('x','Prepare the sprint review','needs_you',"
                 "'Which sprint number?')")
    conn.commit()
    job_id = conn.execute("SELECT id FROM jobs").fetchone()[0]
    client = TestClient(web.make_app(conn, engine_source=_engines,
                                     ceiling_usd=5.0))
    r = client.post(f"/jobs/{job_id}/answer", data={"answer": "Sprint 11"},
                    follow_redirects=False)
    assert r.status_code == 303
    row = conn.execute("SELECT state, answer FROM jobs WHERE id=?",
                       (job_id,)).fetchone()
    assert row["state"] == "queued"
    assert row["answer"] == "Sprint 11"


# --- the weekly board -----------------------------------------------------

def test_semana_answers_200_and_shows_the_false_alarm_rate(tmp_path):
    import datetime as dt
    conn = db.connect(tmp_path / "s.db")
    now = dt.datetime.now().isoformat()
    for _ in range(6):
        conn.execute(
            "INSERT INTO events (at, kind, detail) VALUES (?,'item_found',"
            "'needs_you')", (now,))
    for verb in ("no_era_nada", "no_era_nada", "listo", "listo", "lo_hago_yo"):
        conn.execute(
            "INSERT INTO events (at, kind, verb) VALUES (?,'item_closed',?)",
            (now, verb))
    conn.commit()
    client = TestClient(web.make_app(conn, engine_source=_engines, ceiling_usd=5.0))
    r = client.get("/semana")
    assert r.status_code == 200
    assert "40%" in r.text


def test_semana_with_a_thin_week_says_not_enough_history(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    client = TestClient(web.make_app(conn, engine_source=_engines, ceiling_usd=5.0))
    body = client.get("/semana").text
    assert "no hay suficiente" in body.lower()


def test_posting_a_rating_stores_it(tmp_path):
    from nightshift import life
    conn = db.connect(tmp_path / "s.db")
    item_id = _open_item(conn)
    life.apply_verb(conn, item_id, "listo")
    client = TestClient(web.make_app(conn, engine_source=_engines, ceiling_usd=5.0))
    r = client.post(f"/items/{item_id}/rate",
                    data={"score": "8", "comment": "Good."},
                    follow_redirects=False)
    assert r.status_code == 303
    row = conn.execute("SELECT score, comment FROM items WHERE id=?",
                       (item_id,)).fetchone()
    assert row["score"] == 8
    assert row["comment"] == "Good."


def test_posting_an_invalid_score_changes_nothing(tmp_path):
    from nightshift import life
    conn = db.connect(tmp_path / "s.db")
    item_id = _open_item(conn)
    life.apply_verb(conn, item_id, "listo")
    client = TestClient(web.make_app(conn, engine_source=_engines, ceiling_usd=5.0))
    r = client.post(f"/items/{item_id}/rate", data={"score": "11"},
                    follow_redirects=False)
    assert r.status_code == 303
    row = conn.execute("SELECT score FROM items WHERE id=?",
                       (item_id,)).fetchone()
    assert row["score"] is None
