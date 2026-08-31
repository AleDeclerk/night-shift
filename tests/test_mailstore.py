# tests/test_mailstore.py
from nightshift import db, mailstore


def _insert(conn, *, bucket="needs_you", title="t", body="b",
            source_url="https://x/1", excerpt="", created_at="2026-08-30T00:00:00"):
    conn.execute(
        "INSERT INTO items (run_id, created_at, bucket, title, body,"
        " source_url, excerpt) VALUES (1,?,?,?,?,?,?)",
        (created_at, bucket, title, body, source_url, excerpt))
    conn.commit()


def _conn(tmp_path):
    return db.connect(tmp_path / "state.db")


def test_recent_gives_newest_first_and_respects_limit(tmp_path):
    conn = _conn(tmp_path)
    for i in range(5):
        _insert(conn, title=f"m{i}", created_at=f"2026-08-30T0{i}:00:00")
    result = mailstore.recent(conn, limit=2)
    assert [r["title"] for r in result] == ["m4", "m3"]


def test_recent_filters_by_bucket(tmp_path):
    conn = _conn(tmp_path)
    _insert(conn, bucket="needs_you", title="a")
    _insert(conn, bucket="no_action", title="b")
    result = mailstore.recent(conn, bucket="needs_you")
    assert [r["title"] for r in result] == ["a"]


def test_recent_never_returns_the_excerpt_but_says_whether_one_exists(tmp_path):
    conn = _conn(tmp_path)
    _insert(conn, title="with", excerpt="the message body")
    _insert(conn, title="without", excerpt="")
    result = {r["title"]: r for r in mailstore.recent(conn)}
    assert "excerpt" not in result["with"]
    assert "excerpt" not in result["without"]
    assert result["with"]["has_excerpt"] is True
    assert result["without"]["has_excerpt"] is False


def test_message_gives_the_excerpt(tmp_path):
    conn = _conn(tmp_path)
    _insert(conn, title="m", excerpt="the full first part of the message")
    item_id = conn.execute("SELECT id FROM items").fetchone()[0]
    result = mailstore.message(conn, item_id)
    assert result["excerpt"] == "the full first part of the message"


def test_message_of_an_unknown_id_gives_none(tmp_path):
    conn = _conn(tmp_path)
    assert mailstore.message(conn, 999) is None


def test_search_finds_a_word_in_the_title(tmp_path):
    conn = _conn(tmp_path)
    _insert(conn, title="Shannon: Deck templates", excerpt="")
    _insert(conn, title="AWS bill", excerpt="")
    result = mailstore.search(conn, "Deck")
    assert [r["title"] for r in result] == ["Shannon: Deck templates"]


def test_search_finds_a_word_that_appears_only_in_the_excerpt(tmp_path):
    conn = _conn(tmp_path)
    _insert(conn, title="Invoice", excerpt="the total is 340 USD, due Friday")
    _insert(conn, title="Newsletter", excerpt="no relation here")
    result = mailstore.search(conn, "Friday")
    assert [r["title"] for r in result] == ["Invoice"]


def test_search_ignores_case(tmp_path):
    conn = _conn(tmp_path)
    _insert(conn, title="Shannon: Deck templates", excerpt="")
    result = mailstore.search(conn, "shannon")
    assert len(result) == 1


def test_counts_counts_each_bucket(tmp_path):
    conn = _conn(tmp_path)
    _insert(conn, bucket="needs_you", title="a")
    _insert(conn, bucket="needs_you", title="b")
    _insert(conn, bucket="no_action", title="c")
    assert mailstore.counts(conn) == {"needs_you": 2, "no_action": 1}
