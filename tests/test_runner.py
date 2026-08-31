# tests/test_runner.py
import pathlib

from nightshift import runner

FAKE = pathlib.Path(__file__).parent.parent / "scripts" / "fake-claude"


def test_a_good_run_reports_text_and_cost(monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_MODE", "ok")
    r = runner.run("say hello", binary=str(FAKE), cwd=tmp_path)
    assert r.ok is True
    assert r.text == "hello"
    assert r.cost_usd == 0.012


def test_an_auth_failure_is_a_failure_even_with_exit_code_zero(monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_MODE", "auth")
    r = runner.run("say hello", binary=str(FAKE), cwd=tmp_path)
    assert r.ok is False
    assert "authenticate" in r.error.lower()


def test_output_that_is_not_json_is_a_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_MODE", "junk")
    r = runner.run("say hello", binary=str(FAKE), cwd=tmp_path)
    assert r.ok is False


def test_the_error_of_an_auth_failure_names_the_cause(monkeypatch, tmp_path):
    """The page shows this text, so it must say what went wrong."""
    monkeypatch.setenv("FAKE_MODE", "auth")
    r = runner.run("say hello", binary=str(FAKE), cwd=tmp_path)
    assert r.ok is False
    assert "401" in r.error or "expired" in r.error.lower()
    assert len(r.error) < 400


def test_a_failed_run_reports_no_cost(monkeypatch, tmp_path):
    """A failed run must not move the quota counter."""
    monkeypatch.setenv("FAKE_MODE", "auth")
    assert runner.run("x", binary=str(FAKE), cwd=tmp_path).cost_usd == 0.0


def test_a_good_answer_that_mentions_401_is_not_an_auth_failure(monkeypatch,
                                                                tmp_path):
    """The markers used to scan the raw output before the JSON parse.

    A mail about a 401k plan, or one that quotes an error string, killed a good
    run and threw away its cost. Valid JSON always means the run worked.
    """
    monkeypatch.setenv("FAKE_MODE", "mentions401")
    r = runner.run("read the mail", binary=str(FAKE), cwd=tmp_path)
    assert r.ok is True
    assert r.cost_usd == 0.9
    assert "401k" in r.text


def test_a_killed_run_still_charges_the_governor(monkeypatch, tmp_path):
    """A run that passes the timeout may already have spent. If it reports
    zero, the governor goes blind and the weekly limit burns."""
    monkeypatch.setenv("FAKE_MODE", "ok")
    r = runner.run("x", binary=str(FAKE), cwd=tmp_path, timeout=0)
    assert r.ok is False
    assert r.cost_usd > 0


def test_a_missing_binary_is_a_result_and_not_a_crash(tmp_path):
    """launchd runs with a small PATH. On 2026-08-30 the scheduled cycle died
    with FileNotFoundError: 'claude', which killed the process and left the run
    half written instead of recording the cause."""
    r = runner.run("x", binary="/nowhere/claude", cwd=tmp_path)
    assert r.ok is False
    assert "/nowhere/claude" in r.error


def test_the_default_binary_is_an_absolute_path():
    """A bare name only resolves when PATH holds it, and the PATH of launchd
    does not."""
    assert runner.default_binary().startswith("/")
