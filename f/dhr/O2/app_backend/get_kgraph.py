# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#     "psycopg[binary]==3.3.9",
#     "wmill==1.875.0",
# ]
# ///
"""O2 stage 4, app: a build's knowledge graph (Knowledge graph tab).

Read-only, from the copy each check saves (wiki.kg_nodes, wiki.kg_relations; f/dhr/O2/kgraph.py):
the people, agencies, places and events WeKnora found in the reviews and the relationships
between them. Give doc_id for one review; otherwise every review, the best-connected entities
first, at most max_nodes of them (and the relationships between those).

Fix (25 Nov 2026, problem review "O2 knowledge graph split by review"): WeKnora keeps each
review's entities apart, so the graph was one island per review. With join_reviews (the
default) a named organisation is one entity across the reviews that name it ("Shared by N
reviews"), and each agency links ("Is a") to a node for its type ("Type of organisation"),
so Wiltshire and Bromley Community Safety Partnerships stay two but sit under one
"Community Safety Partnership". People, events and dates stay with their review, because the
same pseudonym in two reviews is two different people (f/dhr/O2/entities.py).
"""

from __future__ import annotations

from collections import Counter

from ...db import apply_migrations, connect
from ..entities import assemble

MAX_NODES = 5000

NODES_SQL = """
SELECT doc_id, name, attributes, n_chunks FROM wiki.kg_nodes
WHERE instance_id = %(id)s AND (%(doc)s = '' OR doc_id = %(doc)s)
"""
RELATIONS_SQL = """
SELECT doc_id, source, target, type FROM wiki.kg_relations
WHERE instance_id = %(id)s AND (%(doc)s = '' OR doc_id = %(doc)s)
"""
REVIEWS_SQL = """
SELECT doc_id, count(*) FROM wiki.kg_nodes WHERE instance_id = %(id)s
GROUP BY doc_id ORDER BY doc_id
"""


def main(instance_id: str, doc_id: str = "", max_nodes: int = 2000, join_reviews: bool = True):
    limit = max(1, min(int(max_nodes or 2000), MAX_NODES))
    args = {"id": instance_id, "doc": doc_id or ""}
    join = bool(join_reviews) and not doc_id
    with connect() as conn:
        apply_migrations(conn)
        node_rows = conn.execute(NODES_SQL, args).fetchall()
        relation_rows = conn.execute(RELATIONS_SQL, args).fetchall()
        reviews = [{"doc_id": d, "n_nodes": c} for d, c in conn.execute(REVIEWS_SQL, args)]

    graph = assemble(node_rows, relation_rows, join)
    entities = graph["nodes"]
    links_all = {(a, b, ty) for (a, b, ty) in graph["relations"]}
    degree: Counter = Counter()
    for a, b, _ in links_all:
        degree[a] += 1
        degree[b] += 1

    order = sorted(entities, key=lambda k: (-degree[k], k))
    kept = set(order[:limit])
    nodes = []
    for key in order[:limit]:
        e = entities[key]
        docs = e["docs"]
        if e["is_type"]:
            where = "Type of organisation"
        elif len(docs) == 1:
            where = docs[0]
        else:
            where = f"Shared by {len(docs)} reviews"
        nodes.append(
            {
                "id": key,
                "doc_id": where,
                "docs": docs,
                "name": e["name"],
                "attributes": e["attributes"],
                "n_chunks": e["n_chunks"],
                "degree": degree[key],
                "is_type": e["is_type"],
            }
        )
    links = [
        {"source": a, "target": b, "type": ty}
        for a, b, ty in sorted(links_all)
        if a in kept and b in kept
    ]
    types: dict[str, int] = {}
    for link in links:
        types[link["type"]] = types.get(link["type"], 0) + 1
    return {
        "nodes": nodes,
        "links": links,
        "reviews": reviews,
        "relation_types": dict(sorted(types.items(), key=lambda kv: -kv[1])),
        "total_nodes": len(entities),
        "truncated": len(entities) > len(nodes),
        "joined": join,
        "shared_entities": sum(
            1 for e in entities.values() if len(e["docs"]) > 1 and not e["is_type"]
        ),
        "organisation_types": sum(1 for e in entities.values() if e["is_type"]),
    }
