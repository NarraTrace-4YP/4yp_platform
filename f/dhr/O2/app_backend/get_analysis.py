# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#     "psycopg[binary]==3.3.9",
#     "wmill==1.875.0",
# ]
# ///
"""O2 stage 4, app: a build's graph analyses (Analysis tab).

Read-only. The build's recent runs (newest first: status, engine, settings, when, errors)
and one run's results: its summary (counts, communities, similar reviews) and its entities,
most important first (PageRank), at most max_nodes of them. run_id 0: the newest finished
run. f/dhr/O2/analysis.py explains the measures; f/dhr/O2/run_analysis starts a run.
"""

from __future__ import annotations

from ...db import apply_migrations, connect

MAX_NODES = 2000

# The top entities by PageRank, plus the top 100 by betweenness and by degree, so the tab
# can sort by any of them.
NODES_SQL = """
WITH ranked AS (
    SELECT *,
        row_number() OVER (ORDER BY pagerank DESC NULLS LAST, degree DESC, name) AS by_pr,
        row_number() OVER (ORDER BY betweenness DESC NULLS LAST, name) AS by_bw,
        row_number() OVER (ORDER BY degree DESC, name) AS by_dg
    FROM wiki.kg_analysis_nodes WHERE run_id = %s
)
SELECT key, name, docs, is_type, degree, pagerank, betweenness, community, component,
    similar_entities
FROM ranked WHERE by_pr <= %s OR by_bw <= 100 OR by_dg <= 100
ORDER BY by_pr
"""


def main(instance_id: str, run_id: int = 0, max_nodes: int = 500):
    limit = max(1, min(int(max_nodes or 500), MAX_NODES))
    with connect() as conn:
        apply_migrations(conn)
        runs = [
            {
                "run_id": r[0],
                "status": r[1],
                "engine": r[2],
                "settings": r[3] or {},
                "error": r[4],
                "started_at": r[5].isoformat() if r[5] else None,
                "finished_at": r[6].isoformat() if r[6] else None,
                "nodes": (r[7] or {}).get("nodes"),
            }
            for r in conn.execute(
                "SELECT run_id, status, engine, settings, error, started_at, finished_at, summary"
                " FROM wiki.kg_analysis_runs WHERE instance_id = %s ORDER BY started_at DESC LIMIT 10",
                (instance_id,),
            )
        ]
        busy = conn.execute(
            "SELECT instance_id FROM wiki.kg_analysis_runs WHERE status = 'running' LIMIT 1"
        ).fetchone()
        has_graph = bool(
            conn.execute(
                "SELECT 1 FROM wiki.kg_nodes WHERE instance_id = %s LIMIT 1", (instance_id,)
            ).fetchone()
        )
        chosen = next(
            (r for r in runs if (r["run_id"] == run_id if run_id else r["status"] == "done")),
            None,
        )
        result = None
        if chosen and chosen["status"] == "done":
            summary = conn.execute(
                "SELECT summary FROM wiki.kg_analysis_runs WHERE run_id = %s", (chosen["run_id"],)
            ).fetchone()[0]
            nodes = [
                {
                    "key": k,
                    "name": n,
                    "docs": d,
                    "is_type": t,
                    "degree": g,
                    "pagerank": p,
                    "betweenness": b,
                    "community": c,
                    "component": o,
                    "similar": s,
                }
                for k, n, d, t, g, p, b, c, o, s in conn.execute(
                    NODES_SQL, (chosen["run_id"], limit)
                )
            ]
            result = {"run": chosen, "summary": summary, "nodes": nodes}
    return {
        "runs": runs,
        "busy_with": busy[0] if busy else None,
        "has_graph": has_graph,
        "result": result,
    }
