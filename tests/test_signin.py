import pathlib

from nightshift import signin

FAKE = pathlib.Path(__file__).parent.parent / "scripts" / "fake-cursor-login"

# The real output of `NO_OPEN_BROWSER=1 cursor-agent login` on 2026-08-31.
REAL_OUTPUT = (
    " Starting login process...\n\n"
    "\x1b[2K\x1b[1A\x1b[2K\x1b[1A\x1b[2K\x1b[G Authenticating with Cursor...\n\n"
    " Waiting for browser authentication...\n"
    " Open a browser and navigate to this link: https://cursor.com/loginDeepControl?\n"
    " challenge=VKAS2FB49ZmxbT9gR_FVXzXlVKnKdMIE0t4aJZAI044&uuid=8d073ba4-0c15-\n"
    " 411c-b7af-17e98d0ea9bd&mode=login&redirectTarget=cli\n"
)


def test_the_link_survives_the_line_breaks_and_the_escapes():
    assert signin.extract_link(REAL_OUTPUT) == (
        "https://cursor.com/loginDeepControl?"
        "challenge=VKAS2FB49ZmxbT9gR_FVXzXlVKnKdMIE0t4aJZAI044"
        "&uuid=8d073ba4-0c15-411c-b7af-17e98d0ea9bd"
        "&mode=login&redirectTarget=cli")


def test_a_half_link_is_not_good_enough():
    """The naive search returns the first line only. That link does nothing."""
    assert not signin.extract_link(REAL_OUTPUT).endswith("?")


def test_output_with_no_link_gives_nothing():
    assert signin.extract_link(" Starting login process...\n") is None


def test_a_started_flow_reports_the_link():
    flow = signin.Flow(binary=str(FAKE))
    flow.start()
    assert flow.wait_for_link(timeout=10).startswith("https://cursor.com/")
    assert flow.state == "waiting"
    flow.cancel()


def test_the_browser_never_opens_by_itself():
    """NO_OPEN_BROWSER keeps the flow quiet, so nothing appears on screen."""
    flow = signin.Flow(binary=str(FAKE))
    flow.start()
    assert flow.env_used.get("NO_OPEN_BROWSER") == "1"
    flow.cancel()


def test_a_cancelled_flow_stops_the_process():
    flow = signin.Flow(binary=str(FAKE))
    flow.start()
    flow.wait_for_link(timeout=10)
    flow.cancel()
    assert flow.state == "cancelled"
    assert flow.process.poll() is not None


def test_the_link_is_never_part_of_the_public_state():
    """The link is a credential. status() feeds the page and any log."""
    flow = signin.Flow(binary=str(FAKE))
    flow.start()
    link = flow.wait_for_link(timeout=10)
    assert link and "challenge" in link
    assert link not in repr(flow.status())
    assert "challenge" not in repr(flow.status())
    flow.cancel()
