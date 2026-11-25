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
between them. Give doc_id for one review; otherwise every review, the best-connected nodes
first, at most max_nodes of them (and the relationships between those).
"""

from __future__ import annotations

from ...db import apply_migrations, connect

MAX_NODES = 5000

NODES_SQL = """
WITH degree AS (
    SELECT doc_id, name, count(*) AS degree FROM (
        SELECT doc_id, source AS name FROM wiki.kg_relations WHERE instance_id = %(id)s
        UNION ALL
        SELECT doc_id, target FROM wiki.kg_relations WHERE instance_id = %(id)s
    ) ends GROUP BY doc_id, name
)
SELECT n.doc_id, n.name, n.attributes, n.n_chunks, coalesce(d.degree, 0) AS degree
FROM wiki.kg_nodes AS n
LEFT JOIN degree AS d USING (doc_id, name)
WHERE n.instance_id = %(id)s AND (%(doc)s = '' OR n.doc_id = %(doc)s)
ORDER BY coalesce(d.degree, 0) DESC, n.doc_id, n.name
LIMIT %(limit)s
"""
RELATIONS_SQL = """
SELECT doc_id, source, target, type FROM wiki.kg_relations
WHERE instance_id = %(id)s AND (%(doc)s = '' OR doc_id = %(doc)s)
"""
REVIEWS_SQL = """
SELECT doc_id, count(*) FROM wiki.kg_nodes WHERE instance_id = %(id)s
GROUP BY doc_id ORDER BY doc_id
"""


def node_id(doc_id: str, name: str) -> str:
    return f"{doc_id}␟{name}"


def main(instance_id: str, doc_id: str = "", max_nodes: int = 2000):
    limit = max(1, min(int(max_nodes or 2000), MAX_NODES))
    args = {"id": instance_id, "doc": doc_id or "", "limit": limit}
    with connect() as conn:
        apply_migrations(conn)
        nodes = [
            {
                "id": node_id(d, n),
                "doc_id": d,
                "name": n,
                "attributes": a or [],
                "n_chunks": c,
                "degree": g,
            }
            for d, n, a, c, g in conn.execute(NODES_SQL, args)
        ]
        kept = {n["id"] for n in nodes}
        links = [
            {"source": node_id(d, s), "target": node_id(d, t), "type": ty}
            for d, s, t, ty in conn.execute(RELATIONS_SQL, args)
            if node_id(d, s) in kept and node_id(d, t) in kept
        ]
        reviews = [{"doc_id": d, "n_nodes": c} for d, c in conn.execute(REVIEWS_SQL, args)]
        total = conn.execute(
            "SELECT count(*) FROM wiki.kg_nodes"
            " WHERE instance_id = %(id)s AND (%(doc)s = '' OR doc_id = %(doc)s)",
            args,
        ).fetchone()[0]
    types: dict[str, int] = {}
    for link in links:
        types[link["type"]] = types.get(link["type"], 0) + 1
    return {
        "nodes": nodes,
        "links": links,
        "reviews": reviews,
        "relation_types": dict(sorted(types.items(), key=lambda kv: -kv[1])),
        "total_nodes": total,
        "truncated": total > len(nodes),
    }
