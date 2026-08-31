"""The mail that the cycle already fetched, for anything that cannot fetch it.

Google's connector only answers pre-registered clients, so the local model can
never reach Gmail. It does not need to: Claude fetches, this module serves.
"""
import sqlite3

LISTING_FIELDS = "id, created_at, bucket, title, body, source_url"


def _listing_row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"], "created_at": row["created_at"],
        "bucket": row["bucket"], "title": row["title"], "body": row["body"],
        "source_url": row["source_url"],
        "has_excerpt": bool(row["excerpt"]),
    }


def recent(conn: sqlite3.Connection, limit: int = 20,
           bucket: str | None = None) -> list[dict]:
    """The newest items first. Never the excerpt itself, so a listing stays
    small; `has_excerpt` says whether `message` has more to show."""
    if bucket:
        rows = conn.execute(
            f"SELECT {LISTING_FIELDS}, excerpt FROM items WHERE bucket = ?"
            " ORDER BY id DESC LIMIT ?", (bucket, limit)).fetchall()
    else:
        rows = conn.execute(
            f"SELECT {LISTING_FIELDS}, excerpt FROM items"
            " ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [_listing_row(r) for r in rows]


def message(conn: sqlite3.Connection, item_id: int) -> dict | None:
    """The listing fields plus the excerpt, or None when the id is unknown."""
    row = conn.execute(
        f"SELECT {LISTING_FIELDS}, excerpt FROM items WHERE id = ?",
        (item_id,)).fetchone()
    if row is None:
        return None
    out = _listing_row(row)
    out["excerpt"] = row["excerpt"] or ""
    return out


def search(conn: sqlite3.Connection, text: str, limit: int = 20) -> list[dict]:
    """Items whose title or excerpt holds `text`. LIKE is case-insensitive
    for ASCII in SQLite, which is enough here."""
    like = f"%{text}%"
    rows = conn.execute(
        f"SELECT {LISTING_FIELDS}, excerpt FROM items"
        " WHERE title LIKE ? OR excerpt LIKE ?"
        " ORDER BY id DESC LIMIT ?", (like, like, limit)).fetchall()
    return [_listing_row(r) for r in rows]


def counts(conn: sqlite3.Connection) -> dict:
    """How many items sit in each bucket."""
    rows = conn.execute(
        "SELECT bucket, count(*) AS n FROM items GROUP BY bucket").fetchall()
    return {r["bucket"]: r["n"] for r in rows}
