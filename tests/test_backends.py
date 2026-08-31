from nightshift import backends


def _fake(answers):
    """It answers a command with a fixed text, and it never runs anything."""
    def run(args, **kw):
        for key, text in answers.items():
            if key in " ".join(args):
                return text
        return ""
    return run


def test_every_engine_reports_a_row():
    names = {e.name for e in backends.check_all(runner=_fake({}))}
    assert names == {"claude", "gemini", "cursor", "ollama"}


def test_cursor_without_a_session_offers_sign_in():
    (cursor,) = [e for e in backends.check_all(
        runner=_fake({"cursor-agent status": "Not logged in"}))
        if e.name == "cursor"]
    assert cursor.signed_in is False
    assert cursor.can_sign_in is True


def test_cursor_with_a_session_does_not_offer_sign_in():
    (cursor,) = [e for e in backends.check_all(
        runner=_fake({"cursor-agent status": "Logged in as ale@example.com"}))
        if e.name == "cursor"]
    assert cursor.signed_in is True
    assert cursor.can_sign_in is False


def test_gemini_sees_no_mail_without_an_mcp_server():
    (gemini,) = [e for e in backends.check_all(
        runner=_fake({"gemini mcp list": "No MCP servers configured."}))
        if e.name == "gemini"]
    assert gemini.sees_mail is False


def test_claude_sees_the_mail_when_a_gmail_server_is_connected():
    (claude,) = [e for e in backends.check_all(
        runner=_fake({"claude mcp list": "claude.ai Gmail: https://x - Connected"}))
        if e.name == "claude"]
    assert claude.sees_mail is True


def test_an_engine_with_no_connector_may_not_do_the_mail_work():
    """This used to say that only Claude could, as a constant. A connector was
    added to Cursor on 2026-08-31, so the answer now follows the measurement."""
    engines = {e.name: e for e in backends.check_all(runner=_fake({}))}
    for name in ("claude", "gemini", "cursor", "ollama"):
        assert engines[name].mail_capable is False, name


def test_the_cursor_check_looks_where_the_runner_works():
    """Cursor approves an MCP server per directory. Measured on 2026-08-31:
    the same command answers `ready` in one directory and `needs approval` in
    another. A check run somewhere else measures the wrong thing."""
    seen = []

    def spy(args, **kw):
        seen.append((tuple(args), kw.get("cwd")))
        return ""

    backends.check_all(runner=spy, workspace="/tmp/somewhere")
    cursor_calls = [cwd for args, cwd in seen if args[0] == "cursor-agent"]
    assert cursor_calls, "the cursor commands never ran"
    assert all(cwd == "/tmp/somewhere" for cwd in cursor_calls), cursor_calls


def test_a_command_that_hangs_leaves_the_state_unknown():
    def hanging(args, **kw):
        raise TimeoutError("the command did not answer")
    (cursor,) = [e for e in backends.check_all(runner=hanging)
                 if e.name == "cursor"]
    assert cursor.signed_in is None  # unknown, never a guess


def test_the_second_call_does_not_run_the_commands_again():
    """Measured on 2026-08-31: `claude mcp list` takes 3.9 seconds, because it
    health-checks ten servers. Without a cache the morning page waits five
    seconds before it draws anything."""
    calls = []

    def counting(args, **kw):
        calls.append(args)
        return ""

    backends.invalidate()
    backends.check_all(runner=counting, use_cache=True)
    first = len(calls)
    backends.check_all(runner=counting, use_cache=True)
    assert len(calls) == first, "the second call ran the commands again"


def test_invalidate_forces_a_fresh_look():
    """After a sign-in the page must show the new state at once."""
    calls = []

    def counting(args, **kw):
        calls.append(args)
        return ""

    backends.invalidate()
    backends.check_all(runner=counting, use_cache=True)
    first = len(calls)
    backends.invalidate()
    backends.check_all(runner=counting, use_cache=True)
    assert len(calls) > first


def test_cursor_sees_the_mail_once_its_connector_is_ready():
    """A connector was added to Cursor on 2026-08-31, so the panel may not
    keep answering NO from a constant."""
    (cursor,) = [e for e in backends.check_all(
        runner=_fake({"cursor-agent mcp list": "gmail: ready"}))
        if e.name == "cursor"]
    assert cursor.sees_mail is True


def test_cursor_without_a_connector_sees_no_mail():
    (cursor,) = [e for e in backends.check_all(
        runner=_fake({"cursor-agent mcp list": "No MCP servers configured"}))
        if e.name == "cursor"]
    assert cursor.sees_mail is False


def test_gemini_sees_the_mail_once_its_connector_is_connected():
    (gemini,) = [e for e in backends.check_all(
        runner=_fake({"gemini mcp list": "gmail: https://x (http) - Connected"}))
        if e.name == "gemini"]
    assert gemini.sees_mail is True


def test_the_mail_work_needs_a_session_and_a_connector():
    """`mail_capable` used to be a constant that named Claude. It must follow
    the measurement instead."""
    engines = {e.name: e for e in backends.check_all(runner=_fake({
        "claude auth status": '{"loggedIn": true}',
        "claude mcp list": "claude.ai Gmail - Connected",
        "cursor-agent status": "Not logged in",
        "cursor-agent mcp list": "gmail: ready"}))}
    assert engines["claude"].mail_capable is True
    # Cursor holds the connector and no session, so it cannot do the work yet.
    assert engines["cursor"].sees_mail is True
    assert engines["cursor"].mail_capable is False
