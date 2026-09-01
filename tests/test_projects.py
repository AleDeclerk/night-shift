import pytest

from nightshift import db, projects


def _make_marker(root, name, marker):
    d = root / name
    (d / marker).mkdir(parents=True)
    return d


def test_discover_finds_a_directory_with_each_marker(tmp_path):
    root = tmp_path / "repos"
    root.mkdir()
    _make_marker(root, "has-wiki", "wiki")
    _make_marker(root, "has-vault", "vault")
    _make_marker(root, "has-graphify", "graphify-out")

    found = {p["name"] for p in projects.discover(roots=[root])}
    assert found == {"has-wiki", "has-vault", "has-graphify"}


def test_discover_skips_a_directory_with_no_marker(tmp_path):
    root = tmp_path / "repos"
    root.mkdir()
    (root / "plain-repo").mkdir()
    _make_marker(root, "has-wiki", "wiki")

    found = {p["name"] for p in projects.discover(roots=[root])}
    assert found == {"has-wiki"}


def test_discover_only_walks_one_level_deep(tmp_path):
    root = tmp_path / "repos"
    root.mkdir()
    nested = root / "outer" / "inner"
    (nested / "wiki").mkdir(parents=True)

    found = {p["name"] for p in projects.discover(roots=[root])}
    assert found == set()


def test_discover_guesses_veritas_from_a_hint_in_the_name(tmp_path):
    root = tmp_path / "repos"
    root.mkdir()
    _make_marker(root, "remarque-qms", "wiki")

    found = {p["name"]: p["scope"] for p in projects.discover(roots=[root])}
    assert found["remarque-qms"] == "veritas"


def test_discover_guesses_personal_with_no_hint(tmp_path):
    root = tmp_path / "repos"
    root.mkdir()
    _make_marker(root, "maradona-vault", "vault")

    found = {p["name"]: p["scope"] for p in projects.discover(roots=[root])}
    assert found["maradona-vault"] == "personal"


def test_discover_points_graph_path_at_an_existing_graph_json(tmp_path):
    root = tmp_path / "repos"
    root.mkdir()
    d = _make_marker(root, "aph-knowledge", "graphify-out")
    graph_file = d / "graphify-out" / "graph.json"
    graph_file.write_text("{}")

    found = {p["name"]: p["graph_path"] for p in projects.discover(roots=[root])}
    assert found["aph-knowledge"] == str(graph_file)


def test_discover_gives_none_when_graph_json_is_absent(tmp_path):
    root = tmp_path / "repos"
    root.mkdir()
    _make_marker(root, "no-graph-yet", "graphify-out")

    found = {p["name"]: p["graph_path"] for p in projects.discover(roots=[root])}
    assert found["no-graph-yet"] is None


def test_sync_writes_the_discovered_projects_and_gives_the_new_count(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    root = tmp_path / "repos"
    root.mkdir()
    _make_marker(root, "remarque-qms", "wiki")
    _make_marker(root, "maradona-vault", "vault")

    new = projects.sync(conn, roots=[root])
    assert new == 2
    names = {r["name"] for r in conn.execute("SELECT name FROM projects")}
    assert names == {"remarque-qms", "maradona-vault"}


def test_sync_never_changes_the_scope_of_a_row_that_already_exists(tmp_path):
    """Hard rule 1 of the design: the first guess is written down, and from
    then on the stored scope wins, even when the guess would differ."""
    conn = db.connect(tmp_path / "s.db")
    root = tmp_path / "repos"
    root.mkdir()
    _make_marker(root, "remarque-qms", "wiki")  # would guess veritas

    projects.sync(conn, roots=[root])
    conn.execute("UPDATE projects SET scope='personal' WHERE name='remarque-qms'")
    conn.commit()

    new = projects.sync(conn, roots=[root])
    assert new == 0
    row = conn.execute(
        "SELECT scope FROM projects WHERE name='remarque-qms'").fetchone()
    assert row["scope"] == "personal"


def test_sync_run_twice_makes_no_duplicate(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    root = tmp_path / "repos"
    root.mkdir()
    _make_marker(root, "remarque-qms", "wiki")

    projects.sync(conn, roots=[root])
    projects.sync(conn, roots=[root])
    count = conn.execute(
        "SELECT count(*) FROM projects WHERE name='remarque-qms'").fetchone()[0]
    assert count == 1


def test_add_makes_a_project_by_hand_with_no_vault(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    project_id = projects.add(conn, "side-project", "personal")
    row = conn.execute("SELECT * FROM projects WHERE id=?",
                       (project_id,)).fetchone()
    assert row["name"] == "side-project"
    assert row["scope"] == "personal"
    assert row["vault_path"] is None


def test_add_refuses_an_unknown_scope(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    with pytest.raises(ValueError):
        projects.add(conn, "side-project", "nowhere")
    assert conn.execute("SELECT count(*) FROM projects").fetchone()[0] == 0


def test_all_projects_filters_by_scope(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    projects.add(conn, "veritas-one", "veritas")
    projects.add(conn, "personal-one", "personal")

    veritas_only = [p["name"] for p in projects.all_projects(conn, scope="veritas")]
    assert veritas_only == ["veritas-one"]

    personal_only = [p["name"] for p in projects.all_projects(conn, scope="personal")]
    assert personal_only == ["personal-one"]

    every_one = {p["name"] for p in projects.all_projects(conn)}
    assert every_one == {"veritas-one", "personal-one"}


def test_all_projects_gives_only_the_active_ones(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    project_id = projects.add(conn, "retired", "personal")
    conn.execute("UPDATE projects SET active=0 WHERE id=?", (project_id,))
    conn.commit()
    assert projects.all_projects(conn) == []


def test_graph_for_gives_the_stored_path(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    conn.execute(
        "INSERT INTO projects (name, scope, graph_path) VALUES"
        " ('aph-knowledge', 'veritas', '/x/graph.json')")
    conn.commit()
    project_id = conn.execute("SELECT id FROM projects").fetchone()[0]
    assert projects.graph_for(conn, project_id) == "/x/graph.json"


def test_graph_for_gives_none_when_the_project_has_no_graph(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    project_id = projects.add(conn, "no-graph", "personal")
    assert projects.graph_for(conn, project_id) is None


# --- .notes: a marker too, but an empty one is not a project --------------

def test_notes_with_a_file_inside_makes_a_project(tmp_path):
    root = tmp_path / "repos"
    root.mkdir()
    d = _make_marker(root, "brailleai", ".notes")
    (d / ".notes" / "handoff.md").write_text("notes")

    found = {p["name"] for p in projects.discover(roots=[root])}
    assert found == {"brailleai"}


def test_notes_with_no_file_inside_makes_no_project(tmp_path):
    root = tmp_path / "repos"
    root.mkdir()
    _make_marker(root, "empty-notes", ".notes")

    found = {p["name"] for p in projects.discover(roots=[root])}
    assert found == set()


def test_notes_with_only_an_empty_subdirectory_makes_no_project(tmp_path):
    """A nested folder is not a file: `.notes/wiki/` with nothing in it must
    not count as the file the marker requires."""
    root = tmp_path / "repos"
    root.mkdir()
    d = _make_marker(root, "nested-empty", ".notes")
    (d / ".notes" / "wiki").mkdir()

    found = {p["name"] for p in projects.discover(roots=[root])}
    assert found == set()


# --- project_paths: one project can live in several folders ---------------

def test_sync_writes_one_path_row_for_each_folder(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    root = tmp_path / "repos"
    root.mkdir()
    d1 = _make_marker(root, "remarque-qms", "wiki")
    d2 = _make_marker(root, "maradona-vault", "vault")

    projects.sync(conn, roots=[root])
    paths = {r["path"] for r in conn.execute("SELECT path FROM project_paths")}
    assert paths == {str(d1), str(d2)}


def test_paths_of_gives_every_folder(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    project_id = projects.add(conn, "aph-knowledge", "veritas")
    conn.execute("INSERT INTO project_paths (project_id, path) VALUES (?,?)",
                (project_id, "/repos/aph-knowledge"))
    conn.execute("INSERT INTO project_paths (project_id, path) VALUES (?,?)",
                (project_id, "/repos/brailleai"))
    conn.commit()

    paths = {r["path"] for r in projects.paths_of(conn, project_id)}
    assert paths == {"/repos/aph-knowledge", "/repos/brailleai"}


def test_graph_for_finds_a_graph_that_lives_in_the_second_folder(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    project_id = projects.add(conn, "aph-knowledge", "veritas")
    conn.execute(
        "INSERT INTO project_paths (project_id, path, graph_path) VALUES"
        " (?,?,NULL)", (project_id, "/repos/aph-knowledge"))
    conn.execute(
        "INSERT INTO project_paths (project_id, path, graph_path) VALUES"
        " (?,?,?)", (project_id, "/repos/brailleai",
                     "/repos/brailleai/graphify-out/graph.json"))
    conn.commit()

    assert projects.graph_for(conn, project_id) == \
        "/repos/brailleai/graphify-out/graph.json"


# --- merge: joining two projects that turned out to be the same work ------

def test_merge_moves_paths_and_jobs_and_hides_the_absorbed_project(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    keep_id = projects.add(conn, "aph-knowledge", "veritas")
    absorb_id = projects.add(conn, "brailleai", "veritas")
    conn.execute("INSERT INTO project_paths (project_id, path) VALUES (?,?)",
                (absorb_id, "/repos/brailleai"))
    conn.execute(
        "INSERT INTO jobs (created_at, prompt, state, project_id, schedule)"
        " VALUES ('x','fix the pipeline','queued',?,'once')", (absorb_id,))
    conn.commit()
    job_id = conn.execute("SELECT id FROM jobs").fetchone()[0]

    projects.merge(conn, keep_id, absorb_id)

    path_row = conn.execute(
        "SELECT project_id FROM project_paths WHERE path='/repos/brailleai'"
    ).fetchone()
    assert path_row["project_id"] == keep_id

    job_row = conn.execute("SELECT project_id FROM jobs WHERE id=?",
                           (job_id,)).fetchone()
    assert job_row["project_id"] == keep_id

    assert [p["id"] for p in projects.all_projects(conn)] == [keep_id]


def test_merge_keeps_the_absorbed_row_and_marks_who_ate_it(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    keep_id = projects.add(conn, "aph-knowledge", "veritas")
    absorb_id = projects.add(conn, "brailleai", "veritas")

    projects.merge(conn, keep_id, absorb_id)

    row = conn.execute("SELECT active, merged_into FROM projects WHERE id=?",
                       (absorb_id,)).fetchone()
    assert row["active"] == 0
    assert row["merged_into"] == keep_id


def test_merge_into_itself_raises(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    pid = projects.add(conn, "solo", "personal")
    with pytest.raises(ValueError):
        projects.merge(conn, pid, pid)
