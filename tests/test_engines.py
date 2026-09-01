import subprocess

import pytest

from nightshift import engines


# --- command_for -----------------------------------------------------------

def test_every_job_engine_has_a_command():
    # "auto" is not a CLI: a caller resolves it through the cascade to one
    # of the other three before it ever reaches command_for.
    for engine in engines.JOB_ENGINES:
        if engine == "auto":
            continue
        assert engines.command_for(engine, "hello") is not None


def test_gemini_has_no_command():
    """The Gemini CLI left the ladder on 2026-09-01, and it never ran a job
    before that either."""
    assert engines.command_for("gemini", "hello") is None


def test_the_ollama_command_runs_the_harness_not_ollama_run():
    """`ollama run` only writes plain text with no sandbox or tools. The
    DeepSeek Harness gives the same local model a sandbox and shell tools."""
    command = engines.command_for("ollama", "hello")
    assert command[0].endswith("dsh")   # absolute path: launchd has no PATH
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

    # The keys are absolute paths now, because launchd carries no PATH.
    def env_of(binary):
        return next(v for k, v in seen_envs.items() if k.endswith(binary))

    assert env_of("dsh").get("OLLAMA_API_KEY") == "ollama"
    assert "OLLAMA_API_KEY" not in env_of("claude")
    assert "OLLAMA_API_KEY" not in env_of("cursor-agent")


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
    for name in ("claude", "cursor"):
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


def test_every_command_names_an_absolute_binary():
    """launchd runs with a small PATH that holds neither Homebrew nor
    ~/.local/bin. On 2026-09-01 the scheduled cycle failed with
    `Cannot start cursor-agent: No such file or directory` while doing a real
    job. The same bug was fixed for `claude` alone, and it bit the next engine.
    """
    for name in engines.JOB_ENGINES:
        if name == "auto":
            continue
        command = engines.command_for(name, "x")
        assert command[0].startswith("/"), f"{name}: {command[0]}"


def test_a_missing_binary_still_names_itself():
    """When nothing resolves, the command keeps the plain name so the error
    says which binary was missing instead of a bare path."""
    assert engines.binary_for("nothing-here") == "nothing-here"


def test_no_command_of_the_work_approves_an_mcp_server():
    """The probes approve a connector to learn whether it answers. The work
    never does: Cursor approves an MCP server per directory, so one
    --approve-mcps in the workspace hands every later compose a send tool.
    """
    for name in engines.JOB_ENGINES:
        if name == "auto":
            continue
        command = engines.command_for(name, "x") or []
        assert "--approve-mcps" not in command, name


def test_a_warning_on_stderr_does_not_reach_the_answer(monkeypatch, tmp_path):
    """The two streams were joined before the parse. One warning line in
    front of the JSON killed the parse and lost the cost with it, and the
    raw JSON plus the warning became the text that `mail.compose` returns,
    which SAVE_PROMPT then saves in a Gmail draft as it stands.
    """
    def fake_run(command, **kw):
        return subprocess.CompletedProcess(
            command, 0,
            stdout='{"result":"Hola Shannon","total_cost_usd":0.42}',
            stderr="warning: config file is deprecated\n")

    monkeypatch.setattr(engines.subprocess, "run", fake_run)
    r = engines.run("hello", engine="cursor", cwd=tmp_path)
    assert r.ok is True
    assert r.cost_usd == 0.42
    assert r.text == "Hola Shannon"
    assert "warning" not in r.text


def test_a_failure_on_stderr_alone_still_fails_the_run(monkeypatch, tmp_path):
    """stdout stays the answer, and stderr stays the error message: an
    engine that prints its refusal on stderr must not pass as a good run."""
    def fake_run(command, **kw):
        return subprocess.CompletedProcess(
            command, 1, stdout="", stderr="Error: not logged in\n")

    monkeypatch.setattr(engines.subprocess, "run", fake_run)
    r = engines.run("hello", engine="cursor", cwd=tmp_path)
    assert r.ok is False
    assert "not logged in" in r.error
