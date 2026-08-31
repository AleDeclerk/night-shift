#!/usr/bin/env python3
"""MCP server over the mail store, for any MCP client.

Read-only: every tool below only reads `mailstore`. None of them writes,
sends or deletes anything, so this server cannot be used to work around
rule 2 of the spec, which forbids the agent from sending mail on its own.
"""
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from fastmcp import FastMCP  # noqa: E402

from nightshift import db, mailstore  # noqa: E402

DEFAULT_DB = pathlib.Path.home() / ".night-shift" / "state.db"
db_path = pathlib.Path(os.environ.get("NIGHTSHIFT_DB", DEFAULT_DB))
db_path.parent.mkdir(parents=True, exist_ok=True)
conn = db.connect(db_path)

mcp = FastMCP("night-shift-mail")


@mcp.tool
def list_mail(bucket: str | None = None, limit: int = 20) -> list[dict]:
    """The newest mail items first, newest first. Never the excerpt."""
    return mailstore.recent(conn, limit=limit, bucket=bucket)


@mcp.tool
def show_mail(item_id: int) -> dict | None:
    """One item, with its excerpt. None when the id is unknown."""
    return mailstore.message(conn, item_id)


@mcp.tool
def search_mail(text: str, limit: int = 20) -> list[dict]:
    """Items whose title or excerpt holds `text`."""
    return mailstore.search(conn, text, limit=limit)


@mcp.tool
def mail_counts() -> dict:
    """How many items sit in each bucket."""
    return mailstore.counts(conn)


if __name__ == "__main__":
    mcp.run(transport="stdio")
