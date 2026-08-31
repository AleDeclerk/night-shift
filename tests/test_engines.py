import subprocess

import pytest

from nightshift import engines


# --- command_for -----------------------------------------------------------

def test_every_job_engine_has_a_command():
    for engine in engines.JOB_ENGINES:
        assert engines.command_for(engine, "hello") is not None


def test_gemini_has_no_command():
    """Gemini is out of service, and it never runs a job."""
    assert engines.command_for("gemini", "hello") is None


def test_the_ollama_command_runs_the_harness_not_ollama_run():
    """`ollama run` only writes plain text with no sandbox or tools. The
    DeepSeek Harness gives the same local model a sandbox and shell tools."""
    command = engines.command_for("ollama", "hello")
    assert command[0] == "dsh"
    assert "--profile" in command and "headless" in command
    assert "run" not in command


# --- env ---------------------------------------------------------------

def test_only_ollama_receives_a_value_in_its_environment(monkeypatch, tmp_path):
    seen_envs = {}

    def fake_run(command, cwd, capture_output, text, timeout, env):
        seen_envs[command[0]] = env
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(engines.subprocess, "run", fake_run)

    for engine in engines.JOB_ENGINES:
        engines.run("hello", engine=engine, cwd=tmp_path)

    assert seen_envs["dsh"].get("OLLAMA_API_KEY") == "ollama"
    assert "OLLAMA_API_KEY" not in seen_envs["claude"]
    assert "OLLAMA_API_KEY" not in seen_envs["cursor-agent"]


# --- run: failure and error paths -------------------------------------

def test_a_text_holding_ineligibletier_fails_the_run(monkeypatch, tmp_path):
    def fake_run(command, **kw):
        return subprocess.CompletedProcess(
            command, 0, stdout="IneligibleTierError: not supported", stderr="")

    monkeypatch.setattr(engines.subprocess, "run", fake_run)
    r = engines.run("hello", engine="claude", cwd=tmp_path)
    assert r.ok is False
    assert r.error is not None


def test_an_os_error_names_the_cause(monkeypatch, tmp_path):
    def fake_run(command, **kw):
        raise OSError("no such file")

    monkeypatch.setattr(engines.subprocess, "run", fake_run)
    r = engines.run("hello", engine="cursor", cwd=tmp_path)
    assert r.ok is False
    assert "no such file" in r.error


def test_a_timeout_names_the_cause(monkeypatch, tmp_path):
    def fake_run(command, **kw):
        raise subprocess.TimeoutExpired(cmd=command, timeout=5)

    monkeypatch.setattr(engines.subprocess, "run", fake_run)
    r = engines.run("hello", engine="ollama", cwd=tmp_path, timeout=5)
    assert r.ok is False
    assert "5" in r.error


def test_an_unknown_engine_fails_without_touching_the_process(monkeypatch,
                                                               tmp_path):
    def fake_run(command, **kw):
        raise AssertionError("must not run a process for an unknown engine")

    monkeypatch.setattr(engines.subprocess, "run", fake_run)
    r = engines.run("hello", engine="gemini", cwd=tmp_path)
    assert r.ok is False
    assert "gemini" in r.error.lower()


# --- parse_job_answer, the heart of the task ----------------------------

def test_parse_job_answer_reads_clean_json():
    data = engines.parse_job_answer('{"finished": true, "summary": "done"}')
    assert data == {"finished": True, "summary": "done"}


def test_parse_job_answer_finds_json_inside_a_fenced_block():
    text = '```json\n{"finished": true, "summary": "done"}\n```'
    data = engines.parse_job_answer(text)
    assert data["finished"] is True
    assert data["summary"] == "done"


def test_parse_job_answer_finds_json_with_prose_before_and_after():
    text = ('Sure, here is the result:\n'
            '{"finished": false, "question": "Which sprint?"}\n'
            'Let me know what you think.')
    data = engines.parse_job_answer(text)
    assert data["finished"] is False
    assert data["question"] == "Which sprint?"


def test_prose_with_no_json_must_not_lose_the_work():
    """A local model that answers well but formats badly must not have its
    work thrown away: prose with no JSON in it becomes a finished job whose
    summary is that same prose."""
    text = "I read the mail and wrote the draft. Everything is done."
    data = engines.parse_job_answer(text)
    assert data["finished"] is True
    assert data["summary"] == text


def test_prose_longer_than_2000_chars_is_trimmed():
    text = "x" * 3000
    data = engines.parse_job_answer(text)
    assert data["finished"] is True
    assert len(data["summary"]) == 2000


def test_empty_text_becomes_a_stopped_job_with_a_question():
    data = engines.parse_job_answer("")
    assert data["finished"] is False
    assert data["question"] == "The engine answered nothing."


def test_whitespace_only_text_is_treated_as_empty():
    data = engines.parse_job_answer("   \n  ")
    assert data["finished"] is False
    assert "question" in data


def test_the_failure_markers_live_in_one_place():
    """Two copies of the same list drift, and the drift never announces
    itself. The day a fifth engine adds a new way to fail, one module would
    learn it and the other would not."""
    from nightshift import backends
    assert engines.FAILURE_MARKERS is backends.FAILURE_MARKERS


def test_no_engine_ever_receives_a_paid_key():
    """Rule 1 of the specification: subscriptions only. An API key would move
    the cost from a flat monthly price to a metered bill."""
    from nightshift import backends
    for name in ("claude", "cursor", "gemini"):
        assert backends.probe_env(name) == {}, name
    # The local one gets a placeholder because the OpenAI-compatible client
    # refuses to start without a value. It buys nothing and it meters nothing.
    assert backends.probe_env("ollama") == {"OLLAMA_API_KEY": "ollama"}


def test_no_command_ever_carries_bare_mode():
    """`--bare` leaves the subscription and asks for ANTHROPIC_API_KEY."""
    from nightshift import backends
    for command in list(backends.PROBE_COMMANDS.values()):
        assert "--bare" not in command, command
    for name in engines.JOB_ENGINES:
        assert "--bare" not in (engines.command_for(name, "x") or []), name
