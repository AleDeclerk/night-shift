# tests/test_web.py
import datetime as dt

from fastapi.testclient import TestClient

from nightshift import backends, db, web

# The page answers the loopback address it binds and nothing else, so every
# test drives it from there. A request with another `Host` is a rebinding
# attack, and the middleware gives it 403.
BASE = f"http://127.0.0.1:{web.PORT}"


def _client(app, **kw):
    kw.setdefault("base_url", BASE)
    return TestClient(app, **kw)


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
    client = _client(web.make_app(conn, engine_source=_engines, ceiling_usd=5.0))
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
    client = _client(web.make_app(conn, engine_source=_engines, ceiling_usd=5.0))
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
    client = _client(web.make_app(conn, engine_source=_engines, ceiling_usd=5.0))
    assert "401" in client.get("/").text


def test_the_page_says_when_the_last_good_cycle_ran(tmp_path):
    """A stale page and a fresh page must not look the same."""
    conn = db.connect(tmp_path / "s.db")
    conn.execute("INSERT INTO runs (started_at, kind, ok)"
                 " VALUES ('2026-08-30T06:30:00','mail',1)")
    conn.commit()
    body = _client(web.make_app(conn, engine_source=_engines, ceiling_usd=5.0)).get("/").text
    assert "30 Aug" in body
    assert "06:30" in body


def test_the_page_says_never_when_no_cycle_ever_ran(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    body = _client(web.make_app(conn, engine_source=_engines, ceiling_usd=5.0)).get("/").text
    assert "never" in body.lower()


def test_a_failed_cycle_does_not_count_as_the_last_good_one(tmp_path):
    """The point of the line is to show that work stopped."""
    conn = db.connect(tmp_path / "s.db")
    conn.execute("INSERT INTO runs (started_at, kind, ok)"
                 " VALUES ('2026-08-30T06:30:00','mail',1)")
    conn.execute("INSERT INTO runs (started_at, kind, ok, error)"
                 " VALUES ('2026-08-31T06:30:00','mail',0,'401 expired')")
    conn.commit()
    body = _client(web.make_app(conn, engine_source=_engines, ceiling_usd=5.0)).get("/").text
    assert "30 Aug" in body  # the good one, not the failed one
    assert "401" in body


def test_opening_an_item_records_the_time(tmp_path):
    """Section 9 of the spec: the count of what you read lives on the page."""
    conn = db.connect(tmp_path / "s.db")
    conn.execute("INSERT INTO runs (started_at, kind, ok) VALUES ('x','mail',1)")
    conn.execute("INSERT INTO items (run_id, created_at, bucket, title,"
                 " source_url) VALUES (1,'x','needs_you','t',"
                 "'https://mail.google.com/mail/u/0/#inbox/m1')")
    conn.commit()
    client = _client(web.make_app(conn, engine_source=_engines, ceiling_usd=5.0))
    r = client.get("/open/1", follow_redirects=False)
    assert r.headers["location"] == "https://mail.google.com/mail/u/0/#inbox/m1"
    assert conn.execute("SELECT opened_at FROM items WHERE id=1"
                        ).fetchone()[0] is not None


def test_open_refuses_a_redirect_to_a_url_outside_gmail_and_this_app(
        tmp_path):
    """The model proposes source_url from a stranger's mail. Without this
    check /open is an open redirect: any URL an item carries would send the
    browser wherever that mail wanted."""
    conn = db.connect(tmp_path / "s.db")
    conn.execute("INSERT INTO runs (started_at, kind, ok) VALUES ('x','mail',1)")
    conn.execute("INSERT INTO items (run_id, created_at, bucket, title,"
                 " source_url) VALUES (1,'x','needs_you','t',"
                 "'https://evil.example/steal')")
    conn.commit()
    client = _client(web.make_app(conn, engine_source=_engines, ceiling_usd=5.0))
    r = client.get("/open/1", follow_redirects=False)
    assert r.status_code == 400
    assert conn.execute("SELECT opened_at FROM items WHERE id=1"
                        ).fetchone()[0] is None


def test_open_redirects_to_a_path_of_this_app(tmp_path):
    """The budget card's source is `/semana`, not a Gmail link."""
    conn = db.connect(tmp_path / "s.db")
    conn.execute("INSERT INTO runs (started_at, kind, ok) VALUES ('x','mail',1)")
    conn.execute("INSERT INTO items (run_id, created_at, bucket, title,"
                 " source_url) VALUES (1,'x','needs_you','t','/semana')")
    conn.commit()
    client = _client(web.make_app(conn, engine_source=_engines, ceiling_usd=5.0))
    r = client.get("/open/1", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/semana"


def test_a_job_that_needs_you_shows_its_question(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    conn.execute("INSERT INTO jobs (created_at, prompt, state, question)"
                 " VALUES ('x','Prepare the sprint review','needs_you',"
                 "'Which sprint number?')")
    conn.commit()
    body = _client(web.make_app(conn, engine_source=_engines, ceiling_usd=5.0)).get("/").text
    assert "Prepare the sprint review" in body
    assert "Which sprint number?" in body


def test_a_queued_job_is_visible_on_the_page(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    conn.execute("INSERT INTO jobs (created_at, prompt, state)"
                 " VALUES ('x','Prepare the sprint review','queued')")
    conn.commit()
    body = _client(web.make_app(conn, engine_source=_engines, ceiling_usd=5.0)).get("/").text
    assert "Prepare the sprint review" in body


def test_a_running_job_shows_when_it_started_apart_from_the_queue(tmp_path):
    """A dead job used to read as `running` right next to the ones still
    waiting, with no way to tell it apart from a job the ladder is really
    working on."""
    conn = db.connect(tmp_path / "s.db")
    conn.execute("INSERT INTO jobs (created_at, prompt, state, started_at)"
                 " VALUES ('x','Long job','running','2026-09-01T06:30:00')")
    conn.execute("INSERT INTO jobs (created_at, prompt, state)"
                 " VALUES ('x','Waiting job','queued')")
    conn.commit()
    body = _client(web.make_app(conn, engine_source=_engines, ceiling_usd=5.0)).get("/").text
    assert "Corriendo desde 06:30" in body
    assert "Long job" in body
    assert "1 en cola" in body    # the running job does not count as queued


def test_a_cycle_that_never_finished_is_visible(tmp_path):
    """A crash between the start and the end leaves `ok` NULL, with no error
    text. The page must not look like a quiet day."""
    conn = db.connect(tmp_path / "s.db")
    conn.execute("INSERT INTO runs (started_at, kind)"
                 " VALUES ('2026-08-30T06:30:00','mail')")
    conn.commit()
    body = _client(web.make_app(conn, engine_source=_engines, ceiling_usd=5.0)).get("/").text
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
    body = _client(web.make_app(conn, engine_source=_engines, ceiling_usd=5.0)).get("/").text
    assert "did not finish" not in body.lower()
    assert "running" in body.lower()


def test_a_cycle_that_started_long_ago_and_never_ended_is_dead(tmp_path):
    import datetime as dt
    conn = db.connect(tmp_path / "s.db")
    old = (dt.datetime.now() - dt.timedelta(hours=3)).isoformat()
    conn.execute("INSERT INTO runs (started_at, kind) VALUES (?, 'mail')",
                 (old,))
    conn.commit()
    body = _client(web.make_app(conn, engine_source=_engines, ceiling_usd=5.0)).get("/").text
    assert "did not finish" in body.lower()


def test_the_machine_room_shows_every_engine(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    body = _client(web.make_app(conn, engine_source=_engines, ceiling_usd=20.0)).get("/").text
    for label in ("Claude", "Gemini", "Cursor", "Ollama"):
        assert label in body


def test_sign_in_refuses_a_get(tmp_path):
    """A prefetch of the browser must not start a login."""
    conn = db.connect(tmp_path / "s.db")
    client = _client(web.make_app(conn, engine_source=_engines, ceiling_usd=20.0))
    assert client.get("/machines/cursor/login").status_code == 405


def test_sign_in_status_answers_before_any_attempt(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    client = _client(web.make_app(conn, engine_source=_engines, ceiling_usd=20.0))
    answer = client.get("/machines/cursor/login/status").json()
    assert answer["state"] == "idle"
    assert answer["has_link"] is False


def test_the_status_payload_carries_no_credential(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    client = _client(web.make_app(conn, engine_source=_engines, ceiling_usd=20.0))
    body = client.get("/machines/cursor/login/status").text
    assert "challenge" not in body
    assert "cursor.com" not in body


def test_cancel_answers_even_with_no_flow(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    client = _client(web.make_app(conn, engine_source=_engines, ceiling_usd=20.0))
    assert client.post("/machines/cursor/login/cancel").json()["state"] == "cancelled"


def test_an_engine_with_no_probe_says_so(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    body = _client(web.make_app(conn, engine_source=_engines,
                                   ceiling_usd=20.0)).get("/").text
    assert "SIN PRUEBA REAL" in body


def test_a_stored_probe_shows_what_the_engine_did(tmp_path):
    from nightshift import backends
    conn = db.connect(tmp_path / "s.db")
    backends.save_probe(conn, backends.ProbeResult(
        "cursor", False, False, 0.0,
        "Incompatible auth server: does not support dynamic client registration"))
    body = _client(web.make_app(conn, engine_source=_engines,
                                   ceiling_usd=20.0)).get("/").text
    assert "Incompatible auth server" in body
    assert "NO PUDO" in body


def test_the_probe_button_names_the_price(tmp_path):
    """The price is quota, not money. A plan of this kind meters no dollars,
    and a button that says USD reads as a charge."""
    conn = db.connect(tmp_path / "s.db")
    body = _client(web.make_app(conn, engine_source=_engines,
                                   ceiling_usd=20.0)).get("/").text
    assert "GASTA CUOTA" in body
    assert "GRATIS" in body


def test_the_page_says_that_the_meter_is_not_a_bill(tmp_path):
    """`total_cost_usd` is what the work would have cost through the API. The
    subscription charges none of it, so the page must not read as an invoice."""
    conn = db.connect(tmp_path / "s.db")
    body = _client(web.make_app(conn, engine_source=_engines,
                                   ceiling_usd=20.0)).get("/").text
    assert "no es un cobro" in body


def test_choosing_an_engine_is_remembered(tmp_path):
    from nightshift import engines
    conn = db.connect(tmp_path / "s.db")
    client = _client(web.make_app(conn, engine_source=_engines,
                                     ceiling_usd=20.0))
    client.post("/machines/engine", data={"name": "ollama"},
                follow_redirects=False)
    assert engines.get_engine(conn) == "ollama"


def test_an_unknown_engine_changes_nothing(tmp_path):
    from nightshift import engines
    conn = db.connect(tmp_path / "s.db")
    client = _client(web.make_app(conn, engine_source=_engines,
                                     ceiling_usd=20.0))
    client.post("/machines/engine", data={"name": "nothing"},
                follow_redirects=False)
    assert engines.get_engine(conn) == "claude"


def test_the_page_marks_the_chosen_engine(tmp_path):
    from nightshift import engines
    conn = db.connect(tmp_path / "s.db")
    engines.set_engine(conn, "ollama")
    body = _client(web.make_app(conn, engine_source=_engines,
                                   ceiling_usd=20.0)).get("/").text
    assert 'value="ollama" selected' in body or "selected" in body


def test_choosing_a_mail_engine_is_remembered(tmp_path):
    from nightshift import engines
    conn = db.connect(tmp_path / "s.db")
    client = _client(web.make_app(conn, engine_source=_engines,
                                     ceiling_usd=20.0))
    client.post("/machines/mail-engine", data={"name": "ollama"},
                follow_redirects=False)
    assert engines.get_mail_engine(conn) == "ollama"


def test_an_unknown_mail_engine_changes_nothing(tmp_path):
    from nightshift import engines
    conn = db.connect(tmp_path / "s.db")
    client = _client(web.make_app(conn, engine_source=_engines,
                                     ceiling_usd=20.0))
    client.post("/machines/mail-engine", data={"name": "nothing"},
                follow_redirects=False)
    assert engines.get_mail_engine(conn) == "claude"


def test_the_page_shows_the_mail_engine_form(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    body = _client(web.make_app(conn, engine_source=_engines,
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

    body = _client(web.make_app(conn, engine_source=marker,
                                   ceiling_usd=20.0)).get("/").text
    assert called, "the page ignored the injected source"
    assert "MOTOR-INYECTADO" in body


def test_the_panel_tells_the_connector_from_the_ability_to_work(tmp_path):
    """`Ve el correo: NO` read as "this engine is useless for mail". Only the
    connector is exclusive: a local model reads the stored mail and writes the
    reply without one."""
    conn = db.connect(tmp_path / "s.db")
    body = _client(web.make_app(conn, engine_source=_engines,
                                   ceiling_usd=20.0)).get("/").text
    assert "Conector de Gmail" in body
    assert "Trabaja el correo" in body


def test_the_bar_shows_every_engine_at_a_glance(tmp_path):
    """One cell used to name only the mail engine. The bar now says which
    engines exist and which one is down, without opening anything."""
    conn = db.connect(tmp_path / "s.db")
    body = _client(web.make_app(conn, engine_source=_engines,
                                   ceiling_usd=20.0)).get("/").text
    bar = body.split('class="bar"')[1].split('class="panel')[0]
    for label in ("Claude", "Gemini", "Cursor", "Ollama"):
        assert label in bar, label
    assert "chip--off" in bar     # Cursor has no session in the fixture


def test_the_bar_says_who_does_what(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    body = _client(web.make_app(conn, engine_source=_engines,
                                   ceiling_usd=20.0)).get("/").text
    assert "Motor del correo" in body
    assert "Motor que redacta" in body
    assert "Motor de tareas" in body


def test_the_room_shows_the_three_roles_together(tmp_path):
    """Fetching, writing and doing jobs are three different jobs, and only the
    first one is locked. Showing the locked one says why it cannot move."""
    conn = db.connect(tmp_path / "s.db")
    body = _client(web.make_app(conn, engine_source=_engines,
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
        client = _client(web.make_app(conn, engine_source=_engines,
                                         ceiling_usd=5.0))
        r = client.post(f"/items/{item_id}/{verb}", follow_redirects=False)
        assert r.status_code == 303
        pendiente = client.get("/").text.split(
            '<section class="panel a-done">')[0]
        assert "Shannon: deck" not in pendiente, verb


def test_an_unknown_verb_answers_without_changing_anything(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    item_id = _open_item(conn)
    client = _client(web.make_app(conn, engine_source=_engines,
                                     ceiling_usd=5.0))
    r = client.post(f"/items/{item_id}/send_it", follow_redirects=False)
    assert r.status_code == 303
    assert conn.execute("SELECT state FROM items WHERE id=?",
                        (item_id,)).fetchone()[0] == "pending"
    assert "Shannon: deck" in client.get("/").text


def test_the_page_shows_the_five_buttons_under_an_item(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    _open_item(conn)
    body = _client(web.make_app(conn, engine_source=_engines,
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
    client = _client(web.make_app(conn, engine_source=_engines,
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
    client = _client(web.make_app(conn, engine_source=_engines,
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
    client = _client(web.make_app(conn, engine_source=_engines, ceiling_usd=5.0))
    r = client.get("/semana")
    assert r.status_code == 200
    assert "40%" in r.text


def test_semana_with_a_thin_week_says_not_enough_history(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    client = _client(web.make_app(conn, engine_source=_engines, ceiling_usd=5.0))
    body = client.get("/semana").text
    assert "no hay suficiente" in body.lower()


def test_posting_a_rating_stores_it(tmp_path):
    from nightshift import life
    conn = db.connect(tmp_path / "s.db")
    item_id = _open_item(conn)
    life.apply_verb(conn, item_id, "listo")
    client = _client(web.make_app(conn, engine_source=_engines, ceiling_usd=5.0))
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
    client = _client(web.make_app(conn, engine_source=_engines, ceiling_usd=5.0))
    r = client.post(f"/items/{item_id}/rate", data={"score": "11"},
                    follow_redirects=False)
    assert r.status_code == 303
    row = conn.execute("SELECT score FROM items WHERE id=?",
                       (item_id,)).fetchone()
    assert row["score"] is None


# --- projects and schedules ----------------------------------------------

def test_posting_a_job_with_a_project_stores_it(tmp_path):
    from nightshift import projects
    conn = db.connect(tmp_path / "s.db")
    project_id = projects.add(conn, "aph-knowledge", "veritas")
    client = _client(web.make_app(conn, engine_source=_engines, ceiling_usd=5.0))
    client.post("/jobs", data={"prompt": "Fix the pipeline",
                               "project_id": str(project_id),
                               "schedule": "once"},
                follow_redirects=False)
    row = conn.execute("SELECT * FROM jobs").fetchone()
    assert row["project_id"] == project_id


def test_the_form_shows_both_scopes(tmp_path):
    from nightshift import projects
    conn = db.connect(tmp_path / "s.db")
    projects.add(conn, "veritas-project", "veritas")
    projects.add(conn, "personal-project", "personal")
    body = _client(web.make_app(conn, engine_source=_engines,
                                   ceiling_usd=5.0)).get("/").text
    assert "Personal" in body
    assert "Veritas" in body
    assert "veritas-project" in body
    assert "personal-project" in body
    assert "Nuevo proyecto" in body


def test_posting_a_new_project_stores_it(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    client = _client(web.make_app(conn, engine_source=_engines, ceiling_usd=5.0))
    r = client.post("/projects", data={"name": "side-project",
                                       "scope": "personal"},
                    follow_redirects=False)
    assert r.status_code == 303
    row = conn.execute("SELECT * FROM projects WHERE name='side-project'"
                       ).fetchone()
    assert row is not None
    assert row["scope"] == "personal"


def test_posting_a_new_project_with_an_unknown_scope_changes_nothing(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    client = _client(web.make_app(conn, engine_source=_engines, ceiling_usd=5.0))
    r = client.post("/projects", data={"name": "side-project",
                                       "scope": "nowhere"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert conn.execute("SELECT count(*) FROM projects").fetchone()[0] == 0


def test_a_job_with_a_project_that_holds_a_graph_shows_its_concepts(
        tmp_path, monkeypatch):
    from nightshift import projects, web as web_module
    conn = db.connect(tmp_path / "s.db")
    graph_path = tmp_path / "graph.json"
    graph_path.write_text("{}")
    project_id = conn.execute(
        "INSERT INTO projects (name, scope, graph_path) VALUES"
        " ('aph-knowledge', 'veritas', ?)", (str(graph_path),)).lastrowid
    conn.commit()
    conn.execute(
        "INSERT INTO jobs (created_at, prompt, state, project_id, schedule)"
        " VALUES ('x','fix the transcription pipeline','queued',?,'once')",
        (project_id,))
    conn.commit()

    monkeypatch.setattr(web_module.knowledge, "about",
                        lambda *a, **kw: [{"label": "BrailleAI Pipeline",
                                          "community": 1, "links": []}])
    body = _client(web.make_app(conn, engine_source=_engines,
                                   ceiling_usd=5.0)).get("/").text
    assert "BrailleAI Pipeline" in body


def test_a_job_with_a_project_that_holds_no_graph_says_so(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    project_id = conn.execute(
        "INSERT INTO projects (name, scope) VALUES ('no-graph', 'personal')"
    ).lastrowid
    conn.commit()
    conn.execute(
        "INSERT INTO jobs (created_at, prompt, state, project_id, schedule)"
        " VALUES ('x','do something','queued',?,'once')", (project_id,))
    conn.commit()
    body = _client(web.make_app(conn, engine_source=_engines,
                                   ceiling_usd=5.0)).get("/").text
    assert "no tiene grafo" in body.lower()


def test_a_job_with_a_graph_in_the_projects_second_folder_shows_its_concepts(
        tmp_path, monkeypatch):
    """`projects.graph_path` only ever holds the first folder a project was
    found in. `graph_for` looks past it, so a folder merged in later can
    hold the only graph the project has."""
    from nightshift import projects, web as web_module
    conn = db.connect(tmp_path / "s.db")
    graph_path = tmp_path / "second" / "graphify-out" / "graph.json"
    graph_path.parent.mkdir(parents=True)
    graph_path.write_text("{}")

    project_id = projects.add(conn, "aph-knowledge", "veritas")
    conn.execute(
        "INSERT INTO project_paths (project_id, path, graph_path) VALUES"
        " (?,?,NULL)", (project_id, str(tmp_path / "first")))
    conn.execute(
        "INSERT INTO project_paths (project_id, path, graph_path) VALUES"
        " (?,?,?)", (project_id, str(tmp_path / "second"), str(graph_path)))
    conn.commit()
    conn.execute(
        "INSERT INTO jobs (created_at, prompt, state, project_id, schedule)"
        " VALUES ('x','fix the transcription pipeline','queued',?,'once')",
        (project_id,))
    conn.commit()

    monkeypatch.setattr(web_module.knowledge, "about",
                        lambda *a, **kw: [{"label": "BrailleAI Pipeline",
                                          "community": 1, "links": []}])
    body = _client(web.make_app(conn, engine_source=_engines,
                                   ceiling_usd=5.0)).get("/").text
    assert "BrailleAI Pipeline" in body


def test_the_machine_room_lists_the_projects(tmp_path):
    """A guessed scope is a guess. The room is where the user corrects it."""
    from nightshift import projects
    conn = db.connect(tmp_path / "s.db")
    projects.add(conn, "APH", "veritas")
    projects.add(conn, "TrasCarton", "personal")
    body = _client(web.make_app(conn, engine_source=_engines,
                                   ceiling_usd=20.0)).get("/").text
    room = body.split('<dialog id="room">')[1]
    assert "APH" in room and "TrasCarton" in room


def test_a_scope_can_be_corrected(tmp_path):
    from nightshift import projects
    conn = db.connect(tmp_path / "s.db")
    pid = projects.add(conn, "Maradona", "veritas")   # guessed wrong
    client = _client(web.make_app(conn, engine_source=_engines,
                                     ceiling_usd=20.0))
    client.post(f"/projects/{pid}", data={"scope": "personal"},
                follow_redirects=False)
    row = conn.execute("SELECT scope FROM projects WHERE id=?", (pid,)).fetchone()
    assert row["scope"] == "personal"


def test_a_project_can_be_retired_without_losing_its_past(tmp_path):
    """Deleting would erase the history of what it did. Retiring hides it."""
    from nightshift import projects
    conn = db.connect(tmp_path / "s.db")
    pid = projects.add(conn, "Viejo", "personal")
    client = _client(web.make_app(conn, engine_source=_engines,
                                     ceiling_usd=20.0))
    client.post(f"/projects/{pid}", data={"active": "0"}, follow_redirects=False)
    assert conn.execute("SELECT count(*) FROM projects").fetchone()[0] == 1
    assert [p["name"] for p in projects.all_projects(conn)] == []


def test_an_unknown_scope_changes_nothing(tmp_path):
    from nightshift import projects
    conn = db.connect(tmp_path / "s.db")
    pid = projects.add(conn, "APH", "veritas")
    client = _client(web.make_app(conn, engine_source=_engines,
                                     ceiling_usd=20.0))
    client.post(f"/projects/{pid}", data={"scope": "nada"}, follow_redirects=False)
    row = conn.execute("SELECT scope FROM projects WHERE id=?", (pid,)).fetchone()
    assert row["scope"] == "veritas"


def test_posting_a_merge_joins_two_projects(tmp_path):
    """APH and BrailleAI are the same work, discovered under two names. The
    room lets the user say so, and the page then shows one entry."""
    from nightshift import projects
    conn = db.connect(tmp_path / "s.db")
    keep_id = projects.add(conn, "aph-knowledge", "veritas")
    absorb_id = projects.add(conn, "brailleai", "veritas")
    conn.execute("INSERT INTO project_paths (project_id, path) VALUES (?,?)",
                (keep_id, "/repos/aph-knowledge"))
    conn.execute("INSERT INTO project_paths (project_id, path) VALUES (?,?)",
                (absorb_id, "/repos/brailleai"))
    conn.commit()
    client = _client(web.make_app(conn, engine_source=_engines,
                                     ceiling_usd=5.0))

    r = client.post(f"/projects/{absorb_id}/merge", data={"into": str(keep_id)},
                    follow_redirects=False)
    assert r.status_code == 303

    row = conn.execute("SELECT active, merged_into FROM projects WHERE id=?",
                       (absorb_id,)).fetchone()
    assert row["active"] == 0
    assert row["merged_into"] == keep_id

    body = client.get("/").text
    room = body.split('<dialog id="room">')[1]
    assert room.count("aph-knowledge") == 1
    assert "brailleai" not in room
    assert "2 carpetas" in room


def test_merging_a_project_into_itself_changes_nothing(tmp_path):
    from nightshift import projects
    conn = db.connect(tmp_path / "s.db")
    pid = projects.add(conn, "solo", "personal")
    client = _client(web.make_app(conn, engine_source=_engines,
                                     ceiling_usd=5.0))

    r = client.post(f"/projects/{pid}/merge", data={"into": str(pid)},
                    follow_redirects=False)
    assert r.status_code == 303
    row = conn.execute("SELECT active, merged_into FROM projects WHERE id=?",
                       (pid,)).fetchone()
    assert row["active"] == 1
    assert row["merged_into"] is None


# --- running the queue on request -----------------------------------------

def test_posting_queue_run_redirects_and_runs_the_queue(tmp_path, monkeypatch):
    """No real engine may ever run in a test, so the module-level call the
    route makes gets replaced, the same way rehacer's does."""
    from nightshift import web as web_module
    conn = db.connect(tmp_path / "s.db")
    calls = []

    def fake_run(conn_arg, **kw):
        calls.append(kw)
        return {"fired": 0, "skipped": 0, "jobs_run": 1, "cost_usd": 0.4,
               "reason": ""}

    monkeypatch.setattr(web_module.tick, "run", fake_run)
    client = _client(web.make_app(conn, engine_source=_engines,
                                     ceiling_usd=5.0))
    r = client.post("/queue/run", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    assert len(calls) == 1


def test_the_page_says_how_many_jobs_wait(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    conn.execute("INSERT INTO jobs (created_at, prompt, state)"
                 " VALUES ('x','one','queued'),('x','two','queued'),"
                 "('x','three','queued')")
    conn.commit()
    body = _client(web.make_app(conn, engine_source=_engines,
                                   ceiling_usd=5.0)).get("/").text
    assert "3 en cola" in body


def test_with_an_empty_queue_the_run_button_is_disabled(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    body = _client(web.make_app(conn, engine_source=_engines,
                                   ceiling_usd=5.0)).get("/").text
    assert "nada en cola" in body
    tag = body[body.index('data-queue-run'):]
    tag = tag[:tag.index(">") + 1]
    assert "disabled" in tag


# --- the wider set of schedules --------------------------------------------

def test_the_schedule_select_shows_the_new_labels(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    body = _client(web.make_app(conn, engine_source=_engines,
                                   ceiling_usd=5.0)).get("/").text
    for label in ("Dos veces por día", "Días de semana", "Cada hora",
                 "Cada 3 horas", "Cada dos semanas"):
        assert label in body, label


# --- one machine, one page ------------------------------------------------

def test_a_post_from_another_site_changes_nothing(tmp_path):
    """A cross-site form POST needs no preflight, so the effect happens
    before any answer is read. `/queue/run`, `/items/{id}/rehacer` and the
    probe all spend the weekly quota, so any page open in the browser could
    burn it."""
    conn = db.connect(tmp_path / "s.db")
    client = _client(web.make_app(conn, engine_source=_engines,
                                  ceiling_usd=5.0))
    r = client.post("/jobs", data={"prompt": "spend my quota"},
                    headers={"origin": "https://evil.example"},
                    follow_redirects=False)
    assert r.status_code == 403
    assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0


def test_a_post_that_the_browser_marks_cross_site_is_refused(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    client = _client(web.make_app(conn, engine_source=_engines,
                                  ceiling_usd=5.0))
    r = client.post("/jobs", data={"prompt": "spend my quota"},
                    headers={"sec-fetch-site": "cross-site"},
                    follow_redirects=False)
    assert r.status_code == 403
    assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0


def test_a_post_with_neither_header_still_works(tmp_path):
    """This is a personal tool, and curl is how the owner drives it."""
    conn = db.connect(tmp_path / "s.db")
    client = _client(web.make_app(conn, engine_source=_engines,
                                  ceiling_usd=5.0))
    r = client.post("/jobs", data={"prompt": "from curl"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1


def test_a_post_from_the_page_itself_works(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    client = _client(web.make_app(conn, engine_source=_engines,
                                  ceiling_usd=5.0))
    r = client.post("/jobs", data={"prompt": "from the page"},
                    headers={"origin": f"http://localhost:{web.PORT}",
                             "sec-fetch-site": "same-origin"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1


def test_a_request_for_another_host_is_refused(tmp_path):
    """Without this check a name that resolves to 127.0.0.1 reaches the page
    from any browser tab, which is how DNS rebinding works."""
    conn = db.connect(tmp_path / "s.db")
    client = _client(web.make_app(conn, engine_source=_engines,
                                  ceiling_usd=5.0),
                     base_url="http://attacker.example")
    assert client.get("/").status_code == 403


def test_the_sign_in_link_never_leaves_this_machine(tmp_path):
    """`POST /machines/cursor/login` answers with the sign-in link, and a
    link of that kind is a credential."""
    conn = db.connect(tmp_path / "s.db")
    client = _client(web.make_app(conn, engine_source=_engines,
                                  ceiling_usd=5.0))
    r = client.post("/machines/cursor/login",
                    headers={"origin": "https://evil.example"})
    assert r.status_code == 403


def test_a_tick_does_not_hide_what_the_mail_cycle_did(tmp_path):
    """The tick writes a run of its own, once an hour. The two lines at the
    top of the page speak about the mail: a tick that ran clean would
    otherwise cover the error of the last cycle and pass as the last good
    one."""
    conn = db.connect(tmp_path / "s.db")
    conn.execute("INSERT INTO runs (started_at, kind, ok)"
                 " VALUES ('2026-08-30T06:30:00','mail',1)")
    conn.execute("INSERT INTO runs (started_at, kind, ok, error)"
                 " VALUES ('2026-08-31T06:30:00','mail',0,"
                 "'401 OAuth access token has expired')")
    conn.execute("INSERT INTO runs (started_at, kind, ok, cost_usd)"
                 " VALUES ('2026-09-01T10:00:00','tick',1,0.4)")
    conn.commit()
    body = _client(web.make_app(conn, engine_source=_engines,
                                ceiling_usd=5.0)).get("/").text
    assert "401" in body        # the error of the mail cycle still shows
    assert "30 Aug" in body     # and the last good cycle is the mail one
    assert "01 Sep" not in body


# --- the price of a probe -------------------------------------------------

def test_a_probe_lands_where_both_governors_read(tmp_path, monkeypatch):
    """A probe costs 0.17 to 0.34 USD of the same quota. `save_probe` wrote
    only the `probes` table, which neither governor reads, so the machine
    room could spend a week of quota one button at a time."""
    from nightshift import backends as backends_module
    from nightshift import cascade, quota

    conn = db.connect(tmp_path / "s.db")

    def fake_probe(name, runner=None, workspace=None):
        return backends.ProbeResult(name, True, True, 0.34, "MAIL-OK")

    monkeypatch.setattr(backends_module, "probe", fake_probe)
    client = _client(web.make_app(conn, engine_source=_engines,
                                  ceiling_usd=20.0))
    answer = client.post("/machines/claude/probe").json()
    assert answer["cost_usd"] == 0.34

    now = dt.datetime.now()
    assert quota.spent_this_week(conn, now) == 0.34
    assert cascade.spent_by(conn, "claude", now) == 0.34

    row = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    assert row["kind"] == "probe"
    assert row["cost_usd"] == 0.34
    assert row["ok"] == 1

    event = conn.execute(
        "SELECT * FROM events WHERE kind='probe_ran'").fetchone()
    assert event["engine"] == "claude"
    assert event["cost_usd"] == 0.34


def test_a_failed_probe_still_records_what_it_spent(tmp_path, monkeypatch):
    from nightshift import backends as backends_module
    from nightshift import quota

    conn = db.connect(tmp_path / "s.db")

    def fake_probe(name, runner=None, workspace=None):
        return backends.ProbeResult(name, False, False, 0.21, "not logged in")

    monkeypatch.setattr(backends_module, "probe", fake_probe)
    client = _client(web.make_app(conn, engine_source=_engines,
                                  ceiling_usd=20.0))
    client.post("/machines/cursor/probe")

    assert quota.spent_this_week(conn, dt.datetime.now()) == 0.21
    row = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    assert row["kind"] == "probe"
    assert row["ok"] == 0


# --- the redo pays the same price as the cycle ----------------------------

def test_a_redo_over_the_ceiling_does_nothing_and_says_why(tmp_path,
                                                           monkeypatch):
    """The route composed and saved with no `quota.may_run` and no
    `cascade.has_room`. So the one button that a person can press many times
    in a row was the only call that walked past the governor."""
    from nightshift import web as web_module

    conn = db.connect(tmp_path / "s.db")
    item_id = _open_item(conn)
    conn.execute("INSERT INTO runs (started_at, kind, ok, cost_usd)"
                 " VALUES (?,'mail',1,9.0)", (dt.datetime.now().isoformat(),))
    conn.commit()

    def never(*a, **kw):
        raise AssertionError("no engine may run once the ceiling is reached")

    monkeypatch.setattr(web_module.mail, "write_draft", never)
    client = _client(web.make_app(conn, engine_source=_engines,
                                  ceiling_usd=5.0))
    r = client.post(f"/items/{item_id}/rehacer", follow_redirects=False)
    assert r.status_code == 303

    refused = conn.execute(
        "SELECT * FROM events WHERE kind='redo_refused'").fetchone()
    assert refused is not None
    assert refused["item_id"] == item_id
    assert refused["detail"]
    body = conn.execute("SELECT body FROM items WHERE id=?",
                        (item_id,)).fetchone()[0]
    assert "Draft" not in body      # nothing was written


def test_a_redo_that_failed_keeps_its_cost_and_says_so(tmp_path, monkeypatch):
    """`if ok:` wrapped the record, so a failed redo dropped the cost and
    left the body untouched. The item went back to `Pendiente` unchanged and
    silent, and the ceiling never saw the spend."""
    from nightshift import cascade, quota
    from nightshift import web as web_module
    from nightshift.mail import DraftResult

    conn = db.connect(tmp_path / "s.db")
    item_id = _open_item(conn)

    def empty_draft(runner_module, item, cwd):
        return DraftResult(0.8, False, "The agent reported an empty draft.")

    monkeypatch.setattr(web_module.mail, "write_draft", empty_draft)
    client = _client(web.make_app(conn, engine_source=_engines,
                                  ceiling_usd=20.0))
    client.post(f"/items/{item_id}/rehacer", follow_redirects=False)

    now = dt.datetime.now()
    assert cascade.spent_by(conn, "claude", now) == 0.8
    assert quota.spent_this_week(conn, now) == 0.8
    body = conn.execute("SELECT body FROM items WHERE id=?",
                        (item_id,)).fetchone()[0]
    assert "NO DRAFT" in body
    assert "empty draft" in body.lower()


def test_a_redo_writes_the_trace_the_way_the_cycle_does(tmp_path, monkeypatch):
    from nightshift import web as web_module
    from nightshift.mail import DraftResult

    conn = db.connect(tmp_path / "s.db")
    item_id = _open_item(conn)

    def good_draft(runner_module, item, cwd):
        return DraftResult(0.4, True, "I asked for the three layouts.")

    monkeypatch.setattr(web_module.mail, "write_draft", good_draft)
    client = _client(web.make_app(conn, engine_source=_engines,
                                  ceiling_usd=20.0))
    client.post(f"/items/{item_id}/rehacer", follow_redirects=False)
    body = conn.execute("SELECT body FROM items WHERE id=?",
                        (item_id,)).fetchone()[0]
    assert body.startswith("She asks for 3 layouts.")
    assert "Draft: I asked for the three layouts." in body

    # A second redo replaces the trace of the first one instead of piling
    # one under the other.
    client.post(f"/items/{item_id}/rehacer", follow_redirects=False)
    body = conn.execute("SELECT body FROM items WHERE id=?",
                        (item_id,)).fetchone()[0]
    assert body.count("Draft: ") == 1


def test_a_redo_that_spent_stops_the_next_one(tmp_path, monkeypatch):
    """`quota.spent_this_week` reads the `runs` table alone. Without a run
    of its own a redo could be pressed all day and the ceiling would never
    bite."""
    from nightshift import web as web_module
    from nightshift.mail import DraftResult

    conn = db.connect(tmp_path / "s.db")
    item_id = _open_item(conn)
    monkeypatch.setattr(
        web_module.mail, "write_draft",
        lambda runner_module, item, cwd: DraftResult(3.0, True, "Redone."))
    client = _client(web.make_app(conn, engine_source=_engines,
                                  ceiling_usd=5.0))
    client.post(f"/items/{item_id}/rehacer", follow_redirects=False)
    client.post(f"/items/{item_id}/rehacer", follow_redirects=False)

    def never(*a, **kw):
        raise AssertionError("the third redo must never reach an engine")

    monkeypatch.setattr(web_module.mail, "write_draft", never)
    client.post(f"/items/{item_id}/rehacer", follow_redirects=False)
    assert conn.execute(
        "SELECT count(*) FROM events WHERE kind='redo_refused'"
    ).fetchone()[0] == 1
