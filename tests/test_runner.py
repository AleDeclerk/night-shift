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
