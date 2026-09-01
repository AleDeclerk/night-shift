"""What the graph of a project already knows, read and never written.

`graphify explain <id>` answers in about a tenth of a second from a local file,
so this costs no tokens. Building a graph does cost, and this module never
builds one.
"""
import json
import pathlib
import re

_WORD_RE = re.compile(r"[a-z0-9]+")

# "the first few links": enough to show the shape of a concept, not the
# whole neighbourhood of a well-connected node.
_LINKS_SHOWN = 5

# Articles, prepositions, conjunctions and pronouns, Spanish and English.
# Left unfiltered, one of these scores a node exactly like a real word such
# as "brailleai" would. Words shorter than three letters are dropped below
# regardless of this list, which is why short function words such as "a",
# "de" or "la" need no entry here.
_STOP_WORDS = frozenset((
    # English
    "the", "and", "for", "with", "from", "this", "that", "these", "those",
    "its", "she", "they", "not", "into", "about", "over", "under", "than",
    "then", "when", "where", "who", "which", "what", "his", "her", "our",
    "their", "him", "them", "are", "was", "were", "been",
    # Spanish
    "los", "del", "las", "por", "una", "con", "como", "pero", "este",
    "esta", "esto", "estos", "estas", "ese", "esa", "eso", "esos", "esas",
    "entre", "cuando", "sin", "sobre", "hasta", "desde", "todo", "toda",
    "todos", "todas", "uno", "unos", "unas", "mis", "tus", "sus", "nos",
    "les", "ellos", "ellas", "nosotros", "nosotras", "vosotros",
    "vosotras", "que", "quien", "quienes", "donde", "porque", "para",
))

_MIN_WORD_LEN = 3


def _words(text: str) -> set[str]:
    found = _WORD_RE.findall(text.lower())
    return {w for w in found
            if len(w) >= _MIN_WORD_LEN and w not in _STOP_WORDS}


def about(graph_path, words, limit: int = 3) -> list[dict]:
    """Match the words of a task against the `label` and `norm_label` of
    every node of the graph at `graph_path`, and give the best matches with
    their label, their community and their first few links.

    A missing project, a missing file or one that fails to parse gives an
    empty list: a project with no graph brings no context, and the job runs
    the same.
    """
    if not graph_path:
        return []
    path = pathlib.Path(graph_path)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return []

    task_words = _words(words if isinstance(words, str) else " ".join(words))
    if not task_words:
        return []

    nodes = data.get("nodes") or []
    links = data.get("links") or []

    scored = []
    for node in nodes:
        label = node.get("label") or ""
        norm_label = node.get("norm_label") or ""
        node_words = _words(label) | _words(norm_label)
        score = len(task_words & node_words)
        if score:
            scored.append((score, node))
    # Python's sort is stable, so nodes that tie on score keep the order the
    # graph gave them instead of shuffling on every call.
    scored.sort(key=lambda pair: pair[0], reverse=True)

    results = []
    for _score, node in scored[:limit]:
        node_id = node.get("id")
        node_links = [
            {"source": link.get("source"), "target": link.get("target"),
             "relation": link.get("relation")}
            for link in links
            if link.get("source") == node_id or link.get("target") == node_id
        ][:_LINKS_SHOWN]
        results.append({
            "label": node.get("label"),
            "community": node.get("community"),
            "links": node_links,
        })
    return results
