# tests/test_ns_mail.py
"""ns-mail is the command the local model runs. It has no way to import
nightshift the way the test suite does, so this drives it as a subprocess,
the way the local model itself would."""
import os
import pathlib
import subprocess
import sys

from nightshift import db

ROOT = pathlib.Path(__file__).parent.parent
SCRIPT = ROOT / "scripts" / "ns-mail"


def _run(db_path, *args):
    env = dict(os.environ, NIGHTSHIFT_DB=str(db_path))
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, env=env, cwd=ROOT)


def test_list_prints_a_stored_title(tmp_path):
    db_path = tmp_path / "state.db"
    conn = db.connect(db_path)
    conn.execute(
        "INSERT INTO items (run_id, created_at, bucket, title, body,"
        " source_url, excerpt) VALUES (1,'2026-08-30T00:00:00','needs_you',"
        "'Shannon: Deck templates','w','https://x/1','the excerpt')")
    conn.commit()
    conn.close()

    done = _run(db_path, "list")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "Shannon: Deck templates" in done.stdout


def test_show_of_an_unknown_id_exits_non_zero(tmp_path):
    db_path = tmp_path / "state.db"
    db.connect(db_path).close()

    done = _run(db_path, "show", "999")
    assert done.returncode == 1
    assert "999" in done.stdout
