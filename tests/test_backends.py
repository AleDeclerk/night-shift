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


def test_only_claude_may_do_the_mail_work():
    engines = {e.name: e for e in backends.check_all(runner=_fake({}))}
    assert engines["claude"].mail_capable is True
    for other in ("gemini", "cursor", "ollama"):
        assert engines[other].mail_capable is False


def test_a_command_that_hangs_leaves_the_state_unknown():
    def hanging(args, **kw):
        raise TimeoutError("the command did not answer")
    (cursor,) = [e for e in backends.check_all(runner=hanging)
                 if e.name == "cursor"]
    assert cursor.signed_in is None  # unknown, never a guess
