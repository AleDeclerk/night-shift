"""The projects, read from the disk instead of from a list somebody keeps.

A directory that holds `wiki/`, `vault/` or `graphify-out/` is a project. The
scope is guessed once and then the stored one wins: a guess that overrides a
decision every night is worse than no guess.
"""
import pathlib
import sqlite3

SEARCH_ROOTS = (pathlib.Path.home() / "repos", pathlib.Path.home() / "Downloads")
MARKERS = ("wiki", "vault", "graphify-out")

# A first guess only. The stored scope always wins after that.
VERITAS_HINTS = ("aph", "braille", "remarque", "scalence", "thinking",
                 "va-", "veritas", "emsl", "iqvia", "repligen", "aivx")

SCOPES = ("personal", "veritas")


def _guess_scope(name: str) -> str:
    lowered = name.lower()
    return "veritas" if any(hint in lowered for hint in VERITAS_HINTS) \
        else "personal"


def discover(roots=None) -> list[dict]:
    """Walk `roots` one level deep and give one dict per directory that
    holds a marker. `graph_path` points at `graphify-out/graph.json` when
    that file exists, else None."""
    roots = SEARCH_ROOTS if roots is None else roots
    found = []
    for root in roots:
        root = pathlib.Path(root)
        if not root.is_dir():
            continue
        for entry in sorted(root.iterdir()):
            if not entry.is_dir():
                continue
            if not any((entry / marker).is_dir() for marker in MARKERS):
                continue
            graph_file = entry / "graphify-out" / "graph.json"
            found.append({
                "name": entry.name,
                "vault_path": str(entry),
                "graph_path": str(graph_file) if graph_file.is_file() else None,
                "scope": _guess_scope(entry.name),
            })
    return found


def sync(conn: sqlite3.Connection, roots=None) -> int:
    """Write the discovered projects. A row that already exists keeps its
    scope (hard rule 1 of the design); its paths are refreshed, since a
    graph can appear later where none existed at first sight."""
    new = 0
    for proj in discover(roots):
        existing = conn.execute(
            "SELECT id FROM projects WHERE name = ?", (proj["name"],)
        ).fetchone()
        if existing is None:
            conn.execute(
                "INSERT INTO projects (name, scope, vault_path, graph_path,"
                " active) VALUES (?,?,?,?,1)",
                (proj["name"], proj["scope"], proj["vault_path"],
                 proj["graph_path"]))
            new += 1
        else:
            conn.execute(
                "UPDATE projects SET vault_path=?, graph_path=? WHERE id=?",
                (proj["vault_path"], proj["graph_path"], existing["id"]))
    conn.commit()
    return new


def all_projects(conn: sqlite3.Connection, scope: str | None = None
                  ) -> list[sqlite3.Row]:
    """The active projects, by name."""
    if scope is not None:
        return conn.execute(
            "SELECT * FROM projects WHERE active = 1 AND scope = ?"
            " ORDER BY name", (scope,)).fetchall()
    return conn.execute(
        "SELECT * FROM projects WHERE active = 1 ORDER BY name").fetchall()


def add(conn: sqlite3.Connection, name: str, scope: str,
        vault_path: str | None = None) -> int:
    """A project made by hand. It needs no vault: it is a name and a scope,
    and it can gain a vault later."""
    if scope not in SCOPES:
        raise ValueError(f"Unknown scope: {scope}")
    cur = conn.execute(
        "INSERT INTO projects (name, scope, vault_path, graph_path, active)"
        " VALUES (?,?,?,NULL,1)", (name, scope, vault_path))
    conn.commit()
    return cur.lastrowid


def graph_for(conn: sqlite3.Connection, project_id: int) -> str | None:
    row = conn.execute("SELECT graph_path FROM projects WHERE id = ?",
                       (project_id,)).fetchone()
    return row["graph_path"] if row else None


def edit(conn, project_id: int, *, scope: str | None = None,
         name: str | None = None, active: bool | None = None) -> None:
    """Correct what the guess got wrong.

    A scope is guessed once from a name, and a name is a poor oracle. This is
    where the user overrides it, and `sync` never touches a stored scope again.
    An unknown scope changes nothing: a bad value must not silently move a
    project to a place the user did not choose.
    """
    if scope is not None:
        if scope not in SCOPES:
            return
        conn.execute("UPDATE projects SET scope=? WHERE id=?", (scope, project_id))
    if name:
        conn.execute("UPDATE projects SET name=? WHERE id=?", (name, project_id))
    if active is not None:
        # Retired, never deleted: the events of what it did stay readable.
        conn.execute("UPDATE projects SET active=? WHERE id=?",
                     (1 if active else 0, project_id))
    conn.commit()
