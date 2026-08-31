"""The Cursor sign-in flow.

The link that this module reads is a credential: it carries a challenge and a
session id. It stays in memory while the flow runs. It never enters the
database and it never enters a log.
"""
import os
import re
import subprocess
import threading
import time

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
DEADLINE_SECONDS = 180

# The reader appends one line at a time, so the buffer holds a third of the
# link for a moment. The link counts as whole once no new output arrives for
# this long. This asks nothing about the names that Cursor chose.
QUIET_SECONDS = 0.4


def extract_link(raw: str) -> str | None:
    """Read the sign-in link out of the CLI output.

    The CLI wraps the link across three lines and mixes ANSI escapes into it.
    A plain search for a URL returns `https://cursor.com/loginDeepControl?`,
    which opens nothing. So the escapes go first, then the wrap.

    This function parses. It does not decide whether the output finished:
    `Flow.candidate_link` owns that, and it decides by quiet time. Tying
    completeness to a parameter name such as `redirectTarget` would break in
    silence the day Cursor renames it.
    """
    clean = ANSI.sub("", raw)
    joined = re.sub(r"\n\s*", "", clean)
    found = re.search(r"https://\S+", joined)
    return found.group(0) if found else None


class Flow:
    """One sign-in attempt. The link never leaves this object."""

    def __init__(self, binary: str = "cursor-agent"):
        self.binary = binary
        self.process = None
        self.state = "idle"      # idle | waiting | done | cancelled | expired
        self.env_used = {}
        self._link = None
        self._buffer = ""
        self._last_data = 0.0
        self._started_at = 0.0
        self._lock = threading.Lock()

    def start(self) -> None:
        self.env_used = dict(os.environ, NO_OPEN_BROWSER="1")
        self.process = subprocess.Popen(
            [self.binary, "login"], stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, env=self.env_used)
        self.state = "waiting"
        self._started_at = time.monotonic()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        for line in self.process.stdout:
            with self._lock:
                self._buffer += line
                self._last_data = time.monotonic()
        with self._lock:
            self._last_data = time.monotonic() - QUIET_SECONDS  # the end is quiet
            if self.state == "waiting":
                self.state = "done" if self.process.poll() == 0 else "expired"

    def candidate_link(self) -> str | None:
        """The link, but only once the output stopped arriving.

        The reader appends line by line, so for a moment the buffer holds only
        the first third of the link. Handing that out gives a dead link.
        """
        if not self._last_data:
            return None
        if time.monotonic() - self._last_data < QUIET_SECONDS:
            return None
        return extract_link(self._buffer)

    def wait_for_link(self, timeout: float = 20) -> str | None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self._lock:
                found = self.candidate_link()
                if found:
                    self._link = found
                    return found
            time.sleep(0.1)
        return None

    def link(self) -> str | None:
        with self._lock:
            return self._link

    def status(self) -> dict:
        """What the page may see. It holds no credential on purpose."""
        expired = (self.state == "waiting"
                   and time.monotonic() - self._started_at > DEADLINE_SECONDS)
        if expired:
            self.cancel()
            self.state = "expired"
        return {"state": self.state, "has_link": self._link is not None,
                "seconds": int(time.monotonic() - self._started_at)}

    def cancel(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        if self.state == "waiting":
            self.state = "cancelled"
