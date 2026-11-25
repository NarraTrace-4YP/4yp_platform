"""O2 stage 4, shared: a copy of a build's knowledge graph in the database, for the O2 app.

WeKnora keeps the knowledge graph (f/dhr/O2/graph.py) in its graph database, weknora-neo4j,
and its API only answers name searches of at most 100 nodes. So each check reads the whole
graph straight from Neo4j and copies it into wiki.kg_nodes and wiki.kg_relations
(migration 016), replacing the build's previous copy, as save_wiki does for the pages. The
O2 app then shows it (Knowledge graph tab) without anyone reaching Neo4j from the website.

How WeKnora stores it (internal/application/repository/retriever/neo4j/repository.go): each
uploaded file's entities are nodes labelled ENTITY<knowledge base id> and
ENTITY<file's knowledge id> (hyphens as underscores), keyed by (name, kg = the file's
knowledge id), with an "attributes" list and the "chunks" they came from; relationships are
typed by the relation names in graph.py. A review uploaded in parts has one set of nodes per
part, so here nodes are merged by (review, name): the same person in two parts of one review
is one node. Nodes from different reviews stay apart, even with the same name, because
pseudonyms such as "Adult A" recur across reviews.

The Neo4j password is the secret variable f/dhr/weknora_graph_password, which
server/windmill.sh sets from WEKNORA_GRAPH_PASSWORD in the server's .env.
"""

from __future__ import annotations

import os
import re

URI = "bolt://weknora-neo4j:7687"
USER = "neo4j"
PASSWORD_PATH = "f/dhr/weknora_graph_password"
_UUID = re.compile(r"^[0-9a-fA-F-]{36}$")


class GraphUnavailable(RuntimeError):
    """The graph database can't be read (no password set, or it isn't running)."""


def label(kb_id: str) -> str:
    """WeKnora's Neo4j label for a knowledge base's entities."""
    if not _UUID.match(kb_id or ""):
        raise ValueError(f"not a knowledge base id: {kb_id!r}")
    return "ENTITY" + kb_id.replace("-", "_")


def password() -> str:
    value = os.environ.get("WEKNORA_GRAPH_PASSWORD")
    if value:
        return value
    try:
        import wmill

        value = wmill.get_variable(PASSWORD_PATH)
    except Exception as error:  # noqa: BLE001 (wmill raises a plain Exception)
        raise GraphUnavailable(
            f"No Neo4j password: {PASSWORD_PATH} isn't set. Run server/update.sh on the server"
            " (it copies WEKNORA_GRAPH_PASSWORD from .env into Windmill)."
        ) from error
    if not value:
        raise GraphUnavailable(f"{PASSWORD_PATH} is empty")
    return value


def fetch_graph(kb_id: str, driver=None) -> tuple[list[dict], list[dict]]:
    """Every entity and relationship WeKnora has stored for this knowledge base:
    ([{name, kg, attributes, n_chunks}], [{source, target, kg, type}])."""
    lab = label(kb_id)
    own = driver is None
    if own:
        try:
            from neo4j import GraphDatabase

            driver = GraphDatabase.driver(
                URI,
                auth=(USER, password()),
                connection_timeout=10,
                # A new build has no entities yet: don't fill the log with "unknown label".
                notifications_min_severity="OFF",
            )
            driver.verify_connectivity()
        except GraphUnavailable:
            raise
        except Exception as error:  # noqa: BLE001
            raise GraphUnavailable(f"can't reach the graph database at {URI}: {error}") from error
    try:
        with driver.session() as session:
            nodes = [
                dict(r)
                for r in session.run(
                    f"MATCH (n:`{lab}`) RETURN n.name AS name, n.kg AS kg,"
                    " coalesce(n.attributes, []) AS attributes,"
                    " size(coalesce(n.chunks, [])) AS n_chunks"
                )
            ]
            relations = [
                dict(r)
                for r in session.run(
                    f"MATCH (a:`{lab}`)-[r]->(b:`{lab}`) WHERE a.kg = b.kg"
                    " RETURN a.name AS source, b.name AS target, a.kg AS kg, type(r) AS type"
                )
            ]
    finally:
        if own:
            driver.close()
    return nodes, relations


def delete_graph(kb_id: str, driver=None) -> int:
    """Removes every entity (and so every relationship) WeKnora stored for this knowledge base,
    a thousand at a time. WeKnora also does this when the knowledge base is deleted; this
    makes sure, for f/dhr/O2/delete_wiki. Returns how many entities were removed."""
    lab = label(kb_id)
    own = driver is None
    if own:
        try:
            from neo4j import GraphDatabase

            driver = GraphDatabase.driver(
                URI,
                auth=(USER, password()),
                connection_timeout=10,
                # A new build has no entities yet: don't fill the log with "unknown label".
                notifications_min_severity="OFF",
            )
            driver.verify_connectivity()
        except GraphUnavailable:
            raise
        except Exception as error:  # noqa: BLE001
            raise GraphUnavailable(f"can't reach the graph database at {URI}: {error}") from error
    removed = 0
    try:
        with driver.session() as session:
            while True:
                record = session.run(
                    f"MATCH (n:`{lab}`) WITH n LIMIT 1000 DETACH DELETE n RETURN count(n) AS n"
                ).single()
                count = int(record["n"]) if record else 0
                removed += count
                if count == 0:
                    break
    finally:
        if own:
            driver.close()
    return removed


def merge_graph(
    nodes: list[dict], relations: list[dict], doc_by_knowledge: dict[str, str]
) -> tuple[list[tuple], list[tuple]]:
    """Merges each review's parts: node rows (doc_id, name, attributes, n_chunks, n_parts)
    and relation rows (doc_id, source, target, type). Entities from files that aren't this
    build's (none expected) are left out."""
    merged: dict[tuple[str, str], dict] = {}
    for n in nodes:
        doc_id = doc_by_knowledge.get(n.get("kg") or "")
        name = (n.get("name") or "").strip()
        if not doc_id or not name:
            continue
        m = merged.setdefault((doc_id, name), {"attributes": [], "n_chunks": 0, "kgs": set()})
        for attribute in n.get("attributes") or []:
            if attribute and attribute not in m["attributes"]:
                m["attributes"].append(attribute)
        m["n_chunks"] += int(n.get("n_chunks") or 0)
        m["kgs"].add(n["kg"])
    node_rows = [
        (doc_id, name, m["attributes"], m["n_chunks"], len(m["kgs"]))
        for (doc_id, name), m in sorted(merged.items())
    ]
    rel_rows = set()
    for r in relations:
        doc_id = doc_by_knowledge.get(r.get("kg") or "")
        source, target = (r.get("source") or "").strip(), (r.get("target") or "").strip()
        if doc_id and source and target and source != target and r.get("type"):
            if (doc_id, source) in merged and (doc_id, target) in merged:
                rel_rows.add((doc_id, source, target, r["type"]))
    return node_rows, sorted(rel_rows)


def save_graph(conn, instance_id: str, nodes: list[dict], relations: list[dict]) -> dict:
    """Replaces the build's copy of its knowledge graph. Returns counts."""
    import json

    doc_by_knowledge = dict(
        conn.execute(
            "SELECT knowledge_id, doc_id FROM wiki.documents"
            " WHERE instance_id = %s AND knowledge_id IS NOT NULL",
            (instance_id,),
        ).fetchall()
    )
    node_rows, rel_rows = merge_graph(nodes, relations, doc_by_knowledge)
    with conn.transaction():
        conn.execute("DELETE FROM wiki.kg_nodes WHERE instance_id = %s", (instance_id,))
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO wiki.kg_nodes (instance_id, doc_id, name, attributes, n_chunks,"
                " n_parts) VALUES (%s, %s, %s, %s, %s, %s)",
                [(instance_id, d, n, json.dumps(a), c, p) for d, n, a, c, p in node_rows],
            )
            cur.executemany(
                "INSERT INTO wiki.kg_relations (instance_id, doc_id, source, target, type)"
                " VALUES (%s, %s, %s, %s, %s)",
                [(instance_id, *row) for row in rel_rows],
            )
    return {"nodes": len(node_rows), "relations": len(rel_rows)}


def copy_graph(conn, instance_id: str, kb_id: str) -> dict:
    """Reads the graph from Neo4j and saves it. Never raises for an unreachable graph
    database: the check that calls this must go on saving the wiki."""
    try:
        nodes, relations = fetch_graph(kb_id)
    except Exception as error:  # noqa: BLE001 (any graph database trouble: report, go on)
        return {"error": str(error)[:500]}
    return save_graph(conn, instance_id, nodes, relations)
