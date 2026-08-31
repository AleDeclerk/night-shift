import datetime as dt

from nightshift import backends, db


def _answer(text):
    def run(args, cwd=None):
        return text
    return run


def test_a_good_probe_reads_the_mail():
    r = backends.probe("claude", runner=_answer(
        '{"result":"MAIL-OK","total_cost_usd":0.42}'))
    assert r.ok is True
    assert r.can_mail is True
    assert r.cost_usd == 0.42


def test_an_engine_that_cannot_run_fails_the_probe():
    """Gemini claims a session and a connector, and it cannot make one call."""
    r = backends.probe("gemini", runner=_answer(
        "IneligibleTierError: This client is no longer supported"))
    assert r.ok is False
    assert r.can_mail is False


def test_a_rejected_connector_fails_the_probe():
    """Cursor says `gmail: ready` and the call comes back rejected."""
    r = backends.probe("cursor", runner=_answer(
        '{"result":"Error: Incompatible auth server: does not support '
        'dynamic client registration"}'))
    assert r.ok is False
    assert r.can_mail is False


def test_an_engine_without_mail_still_answers():
    r = backends.probe("ollama", runner=_answer("NO-MAIL"))
    assert r.ok is True
    assert r.can_mail is False


def test_an_unknown_engine_is_not_a_crash():
    assert backends.probe("nothing", runner=_answer("x")).ok is False


def test_the_detail_never_carries_a_credential():
    r = backends.probe("cursor", runner=_answer(
        '{"result":"see https://cursor.com/login?challenge=SECRET123"}'))
    assert "challenge" not in r.detail
    assert "SECRET123" not in r.detail


def test_a_probe_is_saved_and_read_back(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    backends.save_probe(conn, backends.ProbeResult(
        "claude", True, True, 0.42, "MAIL-OK"))
    rows = backends.last_probes(conn)
    assert rows["claude"]["can_mail"] == 1
    assert rows["claude"]["cost_usd"] == 0.42


def test_only_the_newest_probe_of_each_engine_comes_back(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    backends.save_probe(conn, backends.ProbeResult("claude", False, False, 0.1, "old"))
    backends.save_probe(conn, backends.ProbeResult("claude", True, True, 0.2, "new"))
    rows = backends.last_probes(conn)
    assert len(rows) == 1
    assert rows["claude"]["detail"] == "new"
