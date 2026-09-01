import json

from nightshift import knowledge


def _write_graph(path, nodes, links=None):
    path.write_text(json.dumps({
        "directed": False, "multigraph": False, "graph": {},
        "nodes": nodes, "links": links or [], "hyperedges": [],
    }))


def test_about_finds_a_node_by_its_label(tmp_path):
    graph_path = tmp_path / "graph.json"
    _write_graph(graph_path, [
        {"id": "brailleai", "label": "BrailleAI Transcription Pipeline",
         "norm_label": "brailleai transcription pipeline", "community": 1},
        {"id": "unrelated", "label": "Something else entirely",
         "norm_label": "something else entirely", "community": 4},
    ])
    hits = knowledge.about(str(graph_path), "fix the brailleai pipeline")
    assert len(hits) == 1
    assert hits[0]["label"] == "BrailleAI Transcription Pipeline"
    assert hits[0]["community"] == 1


def test_about_gives_the_links_of_a_matched_node(tmp_path):
    graph_path = tmp_path / "graph.json"
    _write_graph(graph_path, [
        {"id": "brailleai", "label": "BrailleAI", "norm_label": "brailleai",
         "community": 1},
        {"id": "aph", "label": "APH", "norm_label": "aph", "community": 1},
    ], links=[
        {"source": "aph", "target": "brailleai", "relation": "participate_in"},
    ])
    hits = knowledge.about(str(graph_path), "brailleai")
    assert len(hits) == 1
    assert hits[0]["links"] == [
        {"source": "aph", "target": "brailleai", "relation": "participate_in"},
    ]


def test_about_gives_at_most_limit_matches(tmp_path):
    graph_path = tmp_path / "graph.json"
    _write_graph(graph_path, [
        {"id": f"n{i}", "label": f"braille node {i}",
         "norm_label": f"braille node {i}", "community": 1}
        for i in range(5)
    ])
    hits = knowledge.about(str(graph_path), "braille", limit=2)
    assert len(hits) == 2


def test_about_gives_an_empty_list_when_nothing_matches(tmp_path):
    graph_path = tmp_path / "graph.json"
    _write_graph(graph_path, [
        {"id": "x", "label": "Something else", "norm_label": "something else",
         "community": 1},
    ])
    assert knowledge.about(str(graph_path), "brailleai") == []


def test_about_gives_an_empty_list_when_the_file_is_missing(tmp_path):
    missing = tmp_path / "does-not-exist" / "graph.json"
    assert knowledge.about(str(missing), "brailleai") == []


def test_about_gives_an_empty_list_when_the_file_is_unreadable(tmp_path):
    """'Unreadable' here means the JSON is broken, not that the file is
    absent: a graph mid-write or corrupted must not crash a job."""
    graph_path = tmp_path / "graph.json"
    graph_path.write_text("{not valid json")
    assert knowledge.about(str(graph_path), "brailleai") == []


def test_about_gives_an_empty_list_for_no_graph_path(tmp_path):
    assert knowledge.about(None, "brailleai") == []
