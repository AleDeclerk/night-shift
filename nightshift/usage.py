"""The real quota of the subscription, read instead of guessed.

`claude -p /usage` answers in headless mode and it costs nothing. It reports
the share of the week and of the five hour session already used, with the
moment each one resets. Until 2026-09-01 the governor counted its own spend
against a ceiling that somebody typed, because nobody knew this command
answered headless. It does, so the ceiling can be the real one.

Cursor reports no usage (`cursor-agent about` gives only the tier), so its
ceiling stays a count of calls.
"""
import dataclasses
import datetime as dt
import json
import math
import re
import subprocess

LINE = re.compile(
    r"Current (?P<what>session|week \(all models\)): (?P<pct>\d+)% used"
    r" · resets (?P<when>[A-Z][a-z]{2} \d{1,2} at \d{1,2}:\d{2}[ap]m)")


@dataclasses.dataclass(frozen=True)
class Usage:
    week_pct: int
    week_resets: dt.datetime
    session_pct: int
    session_resets: dt.datetime

    def days_left(self, now: dt.datetime) -> int:
        """Whole days until the weekly reset. A part of a day counts as one:
        rounding down would spend the reserve of the last hours."""
        seconds = (self.week_resets - now).total_seconds()
        return max(0, math.ceil(seconds / 86400))

    def allowance_pct(self, now: dt.datetime, reserve_per_day: int = 10) -> int:
        """How many points of the week the system may still use.

        The rule: keep `reserve_per_day` percent of the week for each day left
        before the reset, so the user's own work always has room. What is
        left after the reserve and after what was already used is the
        allowance. Never negative.
        """
        reserve = reserve_per_day * self.days_left(now)
        return max(0, 100 - reserve - self.week_pct)


def _when(text: str, now: dt.datetime) -> dt.datetime:
    """`Sep 8 at 5:59am` into a datetime. The year is the one that puts the
    moment in the future: a reset never lies in the past."""
    # The year goes into the string: parsing a day with no year is ambiguous
    # around leap days, and Python warns about it.
    moment = dt.datetime.strptime(f"{now.year} {text}", "%Y %b %d at %I:%M%p")
    if moment < now - dt.timedelta(days=1):
        moment = moment.replace(year=now.year + 1)
    return moment


def parse(text: str, *, now: dt.datetime) -> Usage | None:
    found = {m.group("what"): m for m in LINE.finditer(text or "")}
    if "session" not in found or "week (all models)" not in found:
        return None
    week, session = found["week (all models)"], found["session"]
    return Usage(
        week_pct=int(week.group("pct")),
        week_resets=_when(week.group("when"), now),
        session_pct=int(session.group("pct")),
        session_resets=_when(session.group("when"), now))


def from_cli_output(raw: str, *, now: dt.datetime) -> Usage | None:
    """The CLI answers a JSON envelope with the text under `result`."""
    try:
        text = json.loads(raw).get("result", "")
    except (json.JSONDecodeError, AttributeError):
        text = raw
    return parse(text, now=now)


def read(*, cwd, binary: str = "claude", timeout: int = 120,
         now: dt.datetime | None = None) -> Usage | None:
    """Ask the CLI. None when it cannot answer, and the caller must then fall
    back to counting: a governor that trusts a blank answer spends blind."""
    now = now or dt.datetime.now()
    try:
        out = subprocess.run([binary, "-p", "/usage", "--output-format", "json"],
                             cwd=cwd, capture_output=True, text=True,
                             timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return from_cli_output(out.stdout or "", now=now)
