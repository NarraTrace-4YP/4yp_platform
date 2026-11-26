"""O2 stage 4, shared: graph analytics on a build's knowledge graph.

Rowan, 25 Nov 2026: analyse the knowledge graphs the wikis make (the people, agencies, places
and events in the reviews and the relationships between them, f/dhr/O2/graph.py) with Neo4j's
Graph Data Science library, and show the results in the O2 app's Analysis tab.

What a run works out, for one build:
- degree: how many other entities each one is linked to;
- PageRank: importance from links, where a link from an important entity counts for more;
- betweenness: how often an entity lies on the shortest path between two others (the
  go-betweens, e.g. an agency most contacts pass through);
- communities (Louvain): groups of entities more linked to each other than to the rest;
- components: the separate pieces of the graph (entities with no path between them);
- similar entities: pairs that are linked to many of the same entities (Jaccard similarity
  of their neighbours, the 3 most similar for each);
- similar reviews (with join_reviews): pairs of reviews that mention many of the same
  named organisations (Jaccard similarity of their joined entities).

Where the graph comes from: the build's copy in the project database (wiki.kg_nodes and
wiki.kg_relations, f/dhr/O2/kgraph.py), not WeKnora's own nodes. WeKnora keeps each review's
entities apart; with join_reviews (the default) the graph is joined as the Knowledge graph
tab shows it: a named organisation is one entity across reviews, each agency links ("Is a")
to a node for its type, and people, events and dates stay with their review
(f/dhr/O2/entities.py says why and how). Type nodes are analysed with the rest but marked,
so the app can leave them out of rankings.
Relationships are counted without direction, and two entities linked several times get
that many as their weight.

Two engines give the same measures:
- "gds": Neo4j's Graph Data Science plugin (compose.yaml, weknora-neo4j). The graph is
  written into Neo4j under its own label, O2A<run id>, never touching WeKnora's entities,
  analysed in memory with the "stream" procedures (which change nothing), and removed again.
- "networkx": the same measures in Python, inside the Windmill job, with no Neo4j at all.
  Betweenness is estimated from 500 sampled starting points for graphs over 2,000 entities.
"auto" uses gds when the plugin answers and networkx otherwise; the run records which.
GDS's PageRank scores aren't scaled to add up to 1 (networkx's are), so compare entities
within a run, not scores between runs of different engines.

Louvain starts from a random point, so community numbers (and occasionally membership) can
differ between runs.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict

from .entities import assemble

ENGINES = ("auto", "gds", "networkx")
MAX_NODES = 50_000  # a run refuses bigger graphs rather than tie up the server
SIMILAR_TOP_K = 3
SIMILAR_CUTOFF = 0.1
BETWEENNESS_SAMPLE_OVER = 2000
BETWEENNESS_SAMPLES = 500
KEEP_RUNS = 5  # per build; older runs are deleted when a new one finishes
SEED = 42


def build_graph(node_rows, relation_rows, join_reviews: bool = True) -> dict:
    """From (doc_id, name, attributes) node rows and (doc_id, source, target, type) relation
    rows: {"nodes": {key: {"name", "docs", "is_type"}}, "edges": {(a, b): {"weight", "types"}}}
    with a < b, joined as the Knowledge graph tab shows it (f/dhr/O2/entities.py: named
    organisations joined across reviews, a node per organisation type, people never joined).
    Relationships lose their direction; a pair linked several times gets that weight."""
    joined = assemble(node_rows, relation_rows, join_reviews)
    nodes = {
        k: {"name": n["name"], "docs": n["docs"], "is_type": n["is_type"]}
        for k, n in joined["nodes"].items()
    }
    edges: dict[tuple, dict] = {}
    for (a, b, rel_type), count in joined["relations"].items():
        pair = (a, b) if a < b else (b, a)
        edge = edges.setdefault(pair, {"weight": 0, "types": Counter()})
        edge["weight"] += count
        edge["types"][rel_type] += count
    return {"nodes": nodes, "edges": edges}


# --- engines: each returns {key: {degree, pagerank, betweenness, community, component,
#     similar: [(other key, score)]}} ------------------------------------------------------


def run_networkx(graph: dict) -> dict:
    import networkx as nx

    g = nx.Graph()
    g.add_nodes_from(graph["nodes"])
    for (a, b), edge in graph["edges"].items():
        g.add_edge(a, b, weight=edge["weight"])
    out = {key: {"degree": g.degree(key), "similar": []} for key in g.nodes}
    if not g.number_of_nodes():
        return out
    pagerank = weighted_pagerank(g)
    sample = BETWEENNESS_SAMPLES if g.number_of_nodes() > BETWEENNESS_SAMPLE_OVER else None
    betweenness = nx.betweenness_centrality(g, k=sample, normalized=False, seed=SEED)
    if sample:  # scale the estimate up to the whole graph, as GDS does
        betweenness = {k: v * g.number_of_nodes() / sample for k, v in betweenness.items()}
    for number, members in enumerate(
        nx.community.louvain_communities(g, weight="weight", seed=SEED)
    ):
        for key in members:
            out[key]["community"] = number
    for number, members in enumerate(nx.connected_components(g)):
        for key in members:
            out[key]["component"] = number
    neighbours = {key: set(g.neighbors(key)) for key in g.nodes}
    for key, mine in neighbours.items():
        scores = []
        # Only entities sharing a neighbour can score above 0.
        for other in {o for n in mine for o in neighbours[n]} - {key}:
            theirs = neighbours[other]
            score = len(mine & theirs) / len(mine | theirs)
            if score >= SIMILAR_CUTOFF:
                scores.append((other, score))
        scores.sort(key=lambda s: (-s[1], s[0]))
        out[key]["similar"] = scores[:SIMILAR_TOP_K]
    for key in out:
        out[key]["pagerank"] = float(pagerank.get(key, 0.0))
        out[key]["betweenness"] = float(betweenness.get(key, 0.0))
    return out


def weighted_pagerank(g, alpha: float = 0.85, tol: float = 1e-10, max_iter: int = 100) -> dict:
    """PageRank on an undirected weighted graph, as GDS and networkx define it, in plain
    Python (networkx's own needs numpy and scipy). An entity with no links shares its score
    out evenly, as networkx does."""
    n = g.number_of_nodes()
    if not n:
        return {}
    strength = {k: sum(d.get("weight", 1) for _, _, d in g.edges(k, data=True)) for k in g}
    rank = dict.fromkeys(g, 1.0 / n)
    for _ in range(max_iter):
        lonely = alpha * sum(rank[k] for k in g if not strength[k]) / n
        new = dict.fromkeys(g, (1.0 - alpha) / n + lonely)
        for k in g:
            if strength[k]:
                share = alpha * rank[k] / strength[k]
                for other, d in g[k].items():
                    new[other] += share * d.get("weight", 1)
        if sum(abs(new[k] - rank[k]) for k in g) < n * tol:
            return new
        rank = new
    return rank


def gds_available(driver) -> str | None:
    """The GDS version, or None if the plugin isn't installed."""
    try:
        with driver.session() as session:
            return session.run("RETURN gds.version() AS v").single()["v"]
    except Exception:  # noqa: BLE001 (no plugin: unknown function)
        return None


def run_gds(graph: dict, run_id: int, driver) -> dict:
    """The same measures with Neo4j's GDS. The graph is written under the label O2A<run_id>
    (never WeKnora's), projected into memory, analysed with stream procedures (which write
    nothing), and both the projection and the nodes are removed afterwards."""
    label = f"O2A{int(run_id)}"
    name = f"o2-analysis-{int(run_id)}"
    keys = list(graph["nodes"])
    n = len(keys)
    out = {key: {"degree": 0, "similar": []} for key in keys}
    if not n:
        return out
    edges = [{"a": a, "b": b, "w": float(e["weight"])} for (a, b), e in graph["edges"].items()]

    def stream(session, query: str, **params):
        return list(session.run(query, name=name, **params))

    with driver.session() as session:
        try:
            for start in range(0, n, 5000):
                session.run(
                    f"UNWIND $keys AS k CREATE (:`{label}` {{key: k}})",
                    keys=keys[start : start + 5000],
                ).consume()
            session.run(
                f"CREATE INDEX o2a_{int(run_id)} IF NOT EXISTS FOR (n:`{label}`) ON (n.key)"
            ).consume()
            session.run("CALL db.awaitIndexes(60)").consume()
            for start in range(0, len(edges), 5000):
                session.run(
                    f"UNWIND $edges AS e MATCH (a:`{label}` {{key: e.a}}), (b:`{label}` {{key: e.b}})"
                    " CREATE (a)-[:LINK {weight: e.w}]->(b)",
                    edges=edges[start : start + 5000],
                ).consume()
            session.run(
                "CALL gds.graph.project($name, $label,"
                " {LINK: {orientation: 'UNDIRECTED', properties: 'weight'}})",
                name=name,
                label=label,
            ).consume()
            as_key = "gds.util.asNode(nodeId).key AS key"
            for r in stream(
                session, f"CALL gds.degree.stream($name) YIELD nodeId, score RETURN {as_key}, score"
            ):
                out[r["key"]]["degree"] = int(r["score"])
            for r in stream(
                session,
                f"CALL gds.pageRank.stream($name, {{relationshipWeightProperty: 'weight'}})"
                f" YIELD nodeId, score RETURN {as_key}, score",
            ):
                out[r["key"]]["pagerank"] = float(r["score"])
            sampling = (
                f", samplingSize: {BETWEENNESS_SAMPLES}, samplingSeed: {SEED}"
                if n > BETWEENNESS_SAMPLE_OVER
                else ""
            )
            for r in stream(
                session,
                f"CALL gds.betweenness.stream($name, {{concurrency: 4{sampling}}})"
                f" YIELD nodeId, score RETURN {as_key}, score",
            ):
                out[r["key"]]["betweenness"] = float(r["score"])
            for r in stream(
                session,
                "CALL gds.louvain.stream($name, {relationshipWeightProperty: 'weight'})"
                f" YIELD nodeId, communityId RETURN {as_key}, communityId",
            ):
                out[r["key"]]["community"] = int(r["communityId"])
            for r in stream(
                session,
                f"CALL gds.wcc.stream($name) YIELD nodeId, componentId RETURN {as_key}, componentId",
            ):
                out[r["key"]]["component"] = int(r["componentId"])
            for r in stream(
                session,
                f"CALL gds.nodeSimilarity.stream($name, {{topK: {SIMILAR_TOP_K},"
                f" similarityCutoff: {SIMILAR_CUTOFF}}})"
                " YIELD node1, node2, similarity"
                " RETURN gds.util.asNode(node1).key AS a, gds.util.asNode(node2).key AS b, similarity",
            ):
                out[r["a"]]["similar"].append((r["b"], float(r["similarity"])))
        finally:
            session.run("CALL gds.graph.drop($name, false)", name=name).consume()
            remove_temporary(session, label, run_id)
    for value in out.values():
        value["similar"].sort(key=lambda s: (-s[1], s[0]))
        value["similar"] = value["similar"][:SIMILAR_TOP_K]
    return out


def remove_temporary(session, label: str, run_id: int) -> None:
    while True:
        deleted = session.run(
            f"MATCH (n:`{label}`) WITH n LIMIT 5000 DETACH DELETE n RETURN count(*) AS c"
        ).single()["c"]
        if not deleted:
            break
    session.run(f"DROP INDEX o2a_{int(run_id)} IF EXISTS").consume()


# --- summary ------------------------------------------------------------------------------


def renumber(results: dict, field: str) -> None:
    """Numbers communities (or components) 1, 2, ... from the largest, so runs read alike."""
    sizes = Counter(r.get(field) for r in results.values() if r.get(field) is not None)
    order = {old: new for new, (old, _) in enumerate(sizes.most_common(), start=1)}
    for r in results.values():
        if r.get(field) is not None:
            r[field] = order[r[field]]


def summarise(graph: dict, results: dict, join_reviews: bool, top: int = 15) -> dict:
    nodes = graph["nodes"]
    by_community: dict[int, list] = defaultdict(list)
    for key, r in results.items():
        if r.get("community") is not None:
            by_community[r["community"]].append(key)
    types_in: dict[int, Counter] = defaultdict(Counter)
    for (a, b), edge in graph["edges"].items():
        ca, cb = results[a].get("community"), results[b].get("community")
        if ca is not None and ca == cb:
            types_in[ca].update(edge["types"])
    communities = []
    for number, members in sorted(by_community.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if len(members) < 2:
            continue
        members.sort(key=lambda k: -results[k].get("pagerank", 0))
        named = [k for k in members if not nodes[k].get("is_type")]
        communities.append(
            {
                "community": number,
                "size": len(members),
                "reviews": len({d for k in members for d in nodes[k]["docs"]}),
                "top": [nodes[k]["name"] for k in named[:6]],
                "types": [nodes[k]["name"] for k in members if nodes[k].get("is_type")][:3],
                "relationships": [t for t, _ in types_in[number].most_common(3)],
            }
        )
        if len(communities) >= top:
            break
    components = Counter(r.get("component") for r in results.values())
    summary = {
        "nodes": len(nodes),
        "links": len(graph["edges"]),
        "components": len(components),
        "largest_component": max(components.values()) if components else 0,
        "communities": len([m for m in by_community.values() if len(m) >= 2]),
        "top_communities": communities,
        "reviews": len({d for n in nodes.values() for d in n["docs"]}),
    }
    if join_reviews:
        summary["shared_entities"] = sum(
            1 for n in nodes.values() if len(n["docs"]) > 1 and not n.get("is_type")
        )
        summary["organisation_types"] = sum(1 for n in nodes.values() if n.get("is_type"))
        summary["similar_reviews"] = similar_reviews(nodes)
    return summary


def similar_reviews(nodes: dict, top: int = 20) -> list[dict]:
    """Pairs of reviews that mention many of the same entities (Jaccard of their entity
    sets). Entities named in more than half of the reviews are left out of the comparison:
    they are generic ("Police", "GP") and would make every review look alike."""
    by_doc: dict[str, set] = defaultdict(set)
    for key, n in nodes.items():
        if n.get("is_type"):  # a type is in every review that has such an agency
            continue
        for d in n["docs"]:
            by_doc[d].add(key)
    n_docs = len(by_doc)
    common = {k for k, n in nodes.items() if n_docs > 2 and len(n["docs"]) > n_docs / 2}
    sets = {d: keys - common for d, keys in by_doc.items()}
    pairs = []
    docs = sorted(sets)
    for i, a in enumerate(docs):
        for b in docs[i + 1 :]:
            shared = sets[a] & sets[b]
            if not shared:
                continue
            score = len(shared) / len(sets[a] | sets[b])
            names = sorted((nodes[k]["name"] for k in shared), key=str.lower)
            pairs.append(
                {
                    "a": a,
                    "b": b,
                    "score": round(score, 4),
                    "shared": len(shared),
                    "examples": names[:6],
                }
            )
    pairs.sort(key=lambda p: (-p["score"], -p["shared"]))
    return pairs[:top]


# --- a run --------------------------------------------------------------------------------


def load_graph(conn, instance_id: str, join_reviews: bool) -> dict:
    node_rows = conn.execute(
        "SELECT doc_id, name, attributes FROM wiki.kg_nodes WHERE instance_id = %s",
        (instance_id,),
    ).fetchall()
    relation_rows = conn.execute(
        "SELECT doc_id, source, target, type FROM wiki.kg_relations WHERE instance_id = %s",
        (instance_id,),
    ).fetchall()
    return build_graph(node_rows, relation_rows, join_reviews)


def save_results(conn, run_id: int, graph: dict, results: dict) -> None:
    nodes = graph["nodes"]
    rows = [
        (
            run_id,
            key,
            nodes[key]["name"],
            json.dumps(nodes[key]["docs"]),
            bool(nodes[key].get("is_type")),
            int(r.get("degree") or 0),
            r.get("pagerank"),
            r.get("betweenness"),
            r.get("community"),
            r.get("component"),
            json.dumps(
                [
                    {"key": o, "name": nodes[o]["name"], "score": round(s, 4)}
                    for o, s in r["similar"]
                ]
            ),
        )
        for key, r in results.items()
    ]
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO wiki.kg_analysis_nodes (run_id, key, name, docs, is_type, degree,"
            " pagerank, betweenness, community, component, similar_entities)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            rows,
        )


def prune_runs(conn, instance_id: str, keep: int = KEEP_RUNS) -> None:
    conn.execute(
        "DELETE FROM wiki.kg_analysis_runs WHERE instance_id = %s AND status <> 'running'"
        " AND run_id NOT IN (SELECT run_id FROM wiki.kg_analysis_runs WHERE instance_id = %s"
        " AND status <> 'running' ORDER BY started_at DESC LIMIT %s)",
        (instance_id, instance_id, keep),
    )


def analyse(
    conn, run_id: int, instance_id: str, engine: str, join_reviews: bool, driver_factory=None
) -> dict:
    """Runs the analysis for a run row already marked running, saves the results and marks
    it done (or failed, with the error, before re-raising)."""
    try:
        graph = load_graph(conn, instance_id, join_reviews)
        if len(graph["nodes"]) > MAX_NODES:
            raise ValueError(
                f"{len(graph['nodes']):,} entities is over this analysis's limit of {MAX_NODES:,}"
            )
        used, detail = "networkx", None
        driver = None
        if engine in ("auto", "gds") and graph["nodes"]:
            try:
                driver = (driver_factory or _neo4j_driver)()
                detail = gds_available(driver)
            except Exception as error:  # noqa: BLE001
                detail, driver = None, None
                if engine == "gds":
                    raise RuntimeError(f"can't reach the graph database: {error}") from error
            if detail:
                used = "gds"
            elif engine == "gds":
                raise RuntimeError(
                    "Neo4j's Graph Data Science plugin isn't installed (compose.yaml,"
                    " weknora-neo4j; then server/update.sh). Use engine auto or networkx."
                )
        try:
            results = run_gds(graph, run_id, driver) if used == "gds" else run_networkx(graph)
        finally:
            if driver is not None:
                driver.close()
        renumber(results, "community")
        renumber(results, "component")
        summary = summarise(graph, results, join_reviews)
        summary["engine_version"] = detail
        with conn.transaction():
            save_results(conn, run_id, graph, results)
            conn.execute(
                "UPDATE wiki.kg_analysis_runs SET status = 'done', engine = %s, summary = %s,"
                " finished_at = now() WHERE run_id = %s",
                (used, json.dumps(summary), run_id),
            )
        prune_runs(conn, instance_id)
        return {"run_id": run_id, "engine": used, **summary}
    except Exception as error:
        conn.execute(
            "UPDATE wiki.kg_analysis_runs SET status = 'failed', error = %s, finished_at = now()"
            " WHERE run_id = %s",
            (str(error)[:2000], run_id),
        )
        raise


def _neo4j_driver():
    from neo4j import GraphDatabase

    from .kgraph import URI, USER, password

    driver = GraphDatabase.driver(
        URI, auth=(USER, password()), connection_timeout=10, notifications_min_severity="OFF"
    )
    driver.verify_connectivity()
    return driver
