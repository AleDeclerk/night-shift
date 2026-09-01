"""The real quota, read from `claude -p /usage`, which costs nothing."""
import datetime as dt

from nightshift import usage

# Verbatim output of `claude -p /usage` on 2026-09-01.
SAMPLE = """You are currently using your subscription to power your Claude Code usage

Current session: 35% used · resets Sep 1 at 8:39pm (America/Buenos_Aires)
Current week (all models): 4% used · resets Sep 8 at 5:59am (America/Buenos_Aires)
Current week (Fable): 5% used · resets Sep 8 at 5:59am (America/Buenos_Aires)

What's contributing to your limits usage?
"""
NOW = dt.datetime(2026, 9, 1, 18, 0)


def test_the_week_and_the_session_are_read():
    u = usage.parse(SAMPLE, now=NOW)
    assert u.week_pct == 4
    assert u.session_pct == 35
    assert u.week_resets == dt.datetime(2026, 9, 8, 5, 59)
    assert u.session_resets == dt.datetime(2026, 9, 1, 20, 39)


def test_a_reset_in_january_belongs_to_next_year():
    text = SAMPLE.replace("Sep 8 at 5:59am", "Jan 3 at 5:59am")
    u = usage.parse(text, now=dt.datetime(2026, 12, 29, 10, 0))
    assert u.week_resets.year == 2027


def test_unreadable_text_gives_none():
    assert usage.parse("Failed to authenticate", now=NOW) is None
    assert usage.parse("", now=NOW) is None


def test_days_left_counts_partial_days_as_whole():
    """The reserve rule keeps 10% for each day left. Six and a half days left
    means seven days of reserve, not six: rounding down would spend the
    reserve of the last half day."""
    u = usage.parse(SAMPLE, now=NOW)
    assert u.days_left(NOW) == 7


def test_the_allowance_follows_the_rule_of_ten_percent_per_day():
    """Week 4% used, seven days left: reserve 70, so the system may use up to
    26 more points of the week."""
    u = usage.parse(SAMPLE, now=NOW)
    assert u.allowance_pct(NOW, reserve_per_day=10) == 26


def test_no_allowance_when_the_reserve_eats_it_all():
    text = SAMPLE.replace("(all models): 4% used", "(all models): 40% used")
    u = usage.parse(text, now=NOW)
    assert u.allowance_pct(NOW, reserve_per_day=10) == 0


def test_reading_the_cli_answer_shape():
    """The CLI wraps the text in a JSON envelope under `result`."""
    envelope = '{"result": %s, "total_cost_usd": 0}' % __import__("json").dumps(SAMPLE)
    u = usage.from_cli_output(envelope, now=NOW)
    assert u is not None and u.week_pct == 4
