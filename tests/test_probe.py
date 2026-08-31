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


def test_the_local_engine_runs_through_the_harness():
    """`ollama run` only writes text. The DeepSeek Harness gives the same local
    model a sandbox and shell tools, and its patch already points at Ollama.
    Measured on 2026-08-31: `dsh --profile headless` answered in 56 seconds."""
    command = backends.PROBE_COMMANDS["ollama"]
    assert command[0] == "dsh"
    assert "--profile" in command and "headless" in command


def test_the_local_engine_gets_its_placeholder_credential():
    """Ollama authenticates nobody, but the OpenAI-compatible client refuses to
    start without some value. This is not a paid API key, and it buys nothing:
    the call stays on this machine."""
    env = backends.probe_env("ollama")
    assert env.get("OLLAMA_API_KEY")


def test_no_other_engine_gets_a_key():
    for name in ("claude", "gemini", "cursor"):
        assert backends.probe_env(name) == {}, name


def test_the_detail_shows_the_cause_and_not_the_startup_noise():
    """A real probe of Gemini on 2026-08-31 filled the page with `YOLO mode is
    enabled` and a keytar warning, while the reason sat further down. The line
    that explains the failure is the one worth 200 characters."""
    noisy = ("YOLO mode is enabled. All tool calls will be automatically "
             "approved.\nKeychain initialization encountered an error: Cannot "
             "find module keytar.node\nRequire stack:\n- /opt/homebrew/x\n"
             "Loaded cached credentials.\n"
             "Error authenticating: IneligibleTierError: This client is no "
             "longer supported for Gemini Code Assist for individuals.\n")
    r = backends.probe("gemini", runner=_answer(noisy))
    assert r.ok is False
    assert "IneligibleTier" in r.detail
    assert "YOLO mode" not in r.detail


def test_a_working_probe_keeps_its_own_answer():
    r = backends.probe("claude", runner=_answer('{"result":"MAIL-OK"}'))
    assert r.detail.startswith("MAIL-OK")


def test_the_claude_probe_carries_its_gmail_permissions():
    """A probe run without --allowedTools is denied in headless mode, and the
    engine answers NO-MAIL honestly. On 2026-08-31 that made the panel say the
    only engine that reads the mail could not read it."""
    from nightshift import mail
    command = backends.PROBE_COMMANDS["claude"]
    assert "--allowedTools" in command
    assert mail.READ_TOOLS in command


def test_the_probe_asks_for_the_reason_of_a_failure():
    """`NO-MAIL` alone hides why. Cursor fails for one reason and a missing
    permission for another, and the page must tell them apart."""
    assert "why" in backends.PROBE_PROMPT.lower()


def test_the_probe_exercises_the_tools_the_system_really_uses():
    """The prompt asked to list labels while the allowed tools were the ones
    the cycle uses to read threads. So the call was denied and the panel said
    Claude could not read the mail. A probe of other tools proves nothing."""
    assert "label" not in backends.PROBE_PROMPT.lower()
    assert "search" in backends.PROBE_PROMPT.lower()


def test_noise_on_stderr_does_not_break_the_json():
    """The probe joined stdout and stderr, so one warning line turned a clean
    JSON answer into unparsable text, and the cost was lost with it."""
    def noisy(args, cwd=None):
        return {"stdout": '{"result":"MAIL-OK","total_cost_usd":0.42}',
                "stderr": "warning: something on stderr\n"}

    r = backends.probe("claude", runner=noisy)
    assert r.can_mail is True
    assert r.cost_usd == 0.42
