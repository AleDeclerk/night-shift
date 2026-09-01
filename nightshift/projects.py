"""The projects, read from the disk instead of from a list somebody keeps.

A directory that holds `wiki/`, `vault/` or `graphify-out/` is a project. The
scope is guessed once and then the stored one wins: a guess that overrides a
decision every night is worse than no guess.
"""
import pathlib
import sqlite3

SEARCH_ROOTS = (pathlib.Path.home() / "repos", pathlib.Path.home() / "Downloads")
MARKERS = ("wiki", "vault", "graphify-out", ".notes")

# `.notes` holds working notes, and an empty one is made by habit, not by
# work: unlike the other markers, it only counts a project in when it holds
# at least one real file somewhere inside it.
_MARKERS_NEEDING_A_FILE = (".notes",)

# A first guess only. The stored scope always wins after that.
VERITAS_HINTS = ("aph", "braille", "remarque", "scalence", "thinking",
                 "va-", "veritas", "emsl", "iqvia", "repligen", "aivx")

SCOPES = ("personal", "veritas")


def _guess_scope(name: str) -> str:
    lowered = name.lower()
    return "veritas" if any(hint in lowered for hint in VERITAS_HINTS) \
        else "personal"


def _marker_present(entry: pathlib.Path, marker: str) -> bool:
    marker_dir = entry / marker
    if not marker_dir.is_dir():
        return False
    if marker in _MARKERS_NEEDING_A_FILE:
        return any(p.is_file() for p in marker_dir.rglob("*"))
    return True


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
            if not any(_marker_present(entry, marker) for marker in MARKERS):
                continue
            graph_file = entry / "graphify-out" / "graph.json"
            found.append({
                "name": entry.name,
                "vault_path": str(entry),
                "graph_path": str(graph_file) if graph_file.is_file() else None,
                "scope": _guess_scope(entry.name),
            })
    return found


def _upsert_path(conn: sqlite3.Connection, project_id: int, path: str,
                  graph_path: str | None) -> None:
    """One row per folder, `path` UNIQUE. A folder already on record keeps
    whatever project it belongs to: a merge (see `merge` below) can have
    moved it there on purpose, and a later `sync` must not undo that by
    walking the disk again."""
    row = conn.execute("SELECT id FROM project_paths WHERE path = ?",
                       (path,)).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO project_paths (project_id, path, graph_path)"
            " VALUES (?,?,?)", (project_id, path, graph_path))
    else:
        conn.execute("UPDATE project_paths SET graph_path=? WHERE id=?",
                     (graph_path, row["id"]))


def sync(conn: sqlite3.Connection, roots=None) -> int:
    """Write the discovered projects. A row that already exists keeps its
    scope (hard rule 1 of the design); its paths are refreshed, since a
    graph can appear later where none existed at first sight.

    `project_paths` gets one row per discovered folder, the first one
    included: it is the table that answers "every folder of this project",
    while `projects.vault_path` and `graph_path` stay exactly as they were,
    holding only the first folder ever found.
    """
    new = 0
    for proj in discover(roots):
        existing = conn.execute(
            "SELECT id FROM projects WHERE name = ?", (proj["name"],)
        ).fetchone()
        if existing is None:
            cur = conn.execute(
                "INSERT INTO projects (name, scope, vault_path, graph_path,"
                " active) VALUES (?,?,?,?,1)",
                (proj["name"], proj["scope"], proj["vault_path"],
                 proj["graph_path"]))
            project_id = cur.lastrowid
            new += 1
        else:
            project_id = existing["id"]
            conn.execute(
                "UPDATE projects SET vault_path=?, graph_path=? WHERE id=?",
                (proj["vault_path"], proj["graph_path"], project_id))
        _upsert_path(conn, project_id, proj["vault_path"], proj["graph_path"])
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
    """The first graph found among all the folders of the project, read in
    the order the folders were added. A folder merged in from elsewhere can
    hold the only graph the project has, so this must look past the first
    folder instead of stopping at `projects.graph_path`."""
    for row in paths_of(conn, project_id):
        if row["graph_path"]:
            return row["graph_path"]
    # No path row has a graph, or the project has no path rows at all (made
    # by hand, or written before `project_paths` existed): the legacy column
    # is the only place left to look.
    row = conn.execute("SELECT graph_path FROM projects WHERE id = ?",
                       (project_id,)).fetchone()
    return row["graph_path"] if row else None


def paths_of(conn: sqlite3.Connection, project_id: int) -> list[sqlite3.Row]:
    """Every folder of a project, in the order they were added."""
    return conn.execute(
        "SELECT * FROM project_paths WHERE project_id = ? ORDER BY id",
        (project_id,)).fetchall()


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


def merge(conn: sqlite3.Connection, keep_id: int, absorb_id: int) -> None:
    """Join two projects that turned out to be the same work.

    Every folder and every job of `absorb_id` moves to `keep_id`. The
    absorbed row is never deleted, only retired and marked with who ate it:
    the events it recorded must stay readable, the same as any other retired
    project.
    """
    if keep_id == absorb_id:
        raise ValueError("A project cannot be merged into itself.")
    conn.execute("UPDATE project_paths SET project_id=? WHERE project_id=?",
                (keep_id, absorb_id))
    conn.execute("UPDATE jobs SET project_id=? WHERE project_id=?",
                (keep_id, absorb_id))
    conn.execute("UPDATE projects SET active=0, merged_into=? WHERE id=?",
                (keep_id, absorb_id))
    conn.commit()
