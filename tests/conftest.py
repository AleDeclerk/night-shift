"""No test ever asks a real CLI.

`quota.read_usage` runs `claude -p /usage`, which takes about three seconds
and needs a signed-in machine. The tick and the cycle call it, so almost
every test in this suite would run it by accident, and the suite would take
minutes instead of seconds. This answers None instead, which is the path a
machine with no session takes. A test that wants a real reading gives its
own with `monkeypatch`.
"""
import pytest

from nightshift import usage


@pytest.fixture(autouse=True)
def never_ask_the_real_cli(monkeypatch):
    monkeypatch.setattr(usage, "read", lambda **kw: None)
