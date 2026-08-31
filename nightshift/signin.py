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


def extract_link(raw: str) -> str | None:
    """Read the sign-in link out of the CLI output.

    The CLI wraps the link across three lines and mixes ANSI escapes into it.
    A plain search for a URL returns `https://cursor.com/loginDeepControl?`,
    which opens nothing. So the escapes go first, then the wrap.

    Only return a link if it looks complete (contains redirectTarget).
    """
    clean = ANSI.sub("", raw)
    joined = re.sub(r"\n\s*", "", clean)
    found = re.search(r"https://\S+", joined)
    if found:
        url = found.group(0)
        # Only return if the URL looks complete (has the expected parameters)
        if "redirectTarget" in url:
            return url
    return None


class Flow:
    """One sign-in attempt. The link never leaves this object."""

    def __init__(self, binary: str = "cursor-agent"):
        self.binary = binary
        self.process = None
        self.state = "idle"      # idle | waiting | done | cancelled | expired
        self.env_used = {}
        self._link = None
        self._buffer = ""
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
                if self._link is None:
                    self._link = extract_link(self._buffer)
        with self._lock:
            if self.state == "waiting":
                self.state = "done" if self.process.poll() == 0 else "expired"

    def wait_for_link(self, timeout: float = 20) -> str | None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self._lock:
                if self._link:
                    return self._link
            time.sleep(0.2)
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
