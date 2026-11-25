# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#     "neo4j==5.28.8",
#     "networkx==3.7",
#     "psycopg[binary]==3.3.9",
#     "wmill==1.875.0",
# ]
# ///
"""O2 stage 4: analyse a build's knowledge graph (centrality, communities, similarity).

Rowan, 25 Nov 2026. f/dhr/O2/analysis.py explains what is worked out and how. Results go to
wiki.kg_analysis_runs / wiki.kg_analysis_nodes and show in the O2 app's Analysis tab, which
can also start a run.

Fix note (25 Nov 2026, problem review "O2 analysis runnable from the public app"): until
now nothing that does work could be started from the website (only Windmill could). Rowan
asked for this one to be: it never touches Blythe or the model, and only reads the build's
saved graph. Because the O2 app is open to anyone with its link, a run is limited: one
analysis at a time across all builds (another is refused until it ends; one stuck for 15
minutes is marked failed), a new run of the same build at most once a minute, a graph of at
most 50,000 entities, and only the choices below (no free text). The Windmill job's own
timeout (set in Windmill) is the last stop.

instance_id: the build. engine: "auto" (Neo4j's GDS if installed, otherwise networkx), "gds"
or "networkx". join_reviews: analyse the graph joined as the Knowledge graph tab shows it: a
named organisation is one entity across reviews and each agency links to a node for its
type; people, events and dates stay with their review (f/dhr/O2/entities.py). This links
the reviews into one graph and is needed for "similar reviews".
"""

from __future__ import annotations

import json
import os

from ...db import apply_migrations, connect
from ..analysis import ENGINES, analyse
from ..wiki_store import get_instance

STUCK_MINUTES = 15
SAME_BUILD_GAP_SECONDS = 60
LOCK = 727_017  # advisory lock id for starting a run


class Busy(RuntimeError):
    pass


def requester() -> str:
    return (
        os.environ.get("WM_END_USER_EMAIL")
        or (f"{os.environ['WM_USERNAME']}" if os.environ.get("WM_USERNAME") else "")
        or "unknown"
    )


def start_run(conn, instance_id: str, settings: dict) -> int:
    """Records a new running run, or raises Busy. Checked and recorded under one lock, so
    two clicks at once can't both start."""
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK,))
        conn.execute(
            "UPDATE wiki.kg_analysis_runs SET status = 'failed', finished_at = now(),"
            " error = %s"
            " WHERE status = 'running' AND started_at < now() - make_interval(mins => %s)",
            (
                f"stopped: still running after {STUCK_MINUTES} minutes (its job probably ended)",
                STUCK_MINUTES,
            ),
        )
        running = conn.execute(
            "SELECT instance_id FROM wiki.kg_analysis_runs WHERE status = 'running' LIMIT 1"
        ).fetchone()
        if running:
            raise Busy(
                f"Another analysis is running (for {running[0]}). Try again when it has finished."
            )
        recent = conn.execute(
            "SELECT 1 FROM wiki.kg_analysis_runs WHERE instance_id = %s"
            " AND started_at > now() - make_interval(secs => %s)",
            (instance_id, SAME_BUILD_GAP_SECONDS),
        ).fetchone()
        if recent:
            raise Busy("This wiki was analysed less than a minute ago. Try again shortly.")
        return conn.execute(
            "INSERT INTO wiki.kg_analysis_runs (instance_id, settings, requested_by)"
            " VALUES (%s, %s, %s) RETURNING run_id",
            (instance_id, json.dumps(settings), requester()),
        ).fetchone()[0]


def main(instance_id: str, engine: str = "auto", join_reviews: bool = True):
    if engine not in ENGINES:
        raise ValueError(f"engine must be one of {', '.join(ENGINES)}")
    join_reviews = bool(join_reviews)
    with connect() as conn:
        apply_migrations(conn)
        get_instance(conn, instance_id)  # an unknown build fails here
        nodes = conn.execute(
            "SELECT count(*) FROM wiki.kg_nodes WHERE instance_id = %s", (instance_id,)
        ).fetchone()[0]
        if not nodes:
            raise ValueError(
                "This wiki has no knowledge graph yet (built with the graph off, or its graph"
                " calls haven't run yet)."
            )
        run_id = start_run(conn, instance_id, {"engine": engine, "join_reviews": join_reviews})
        print(f"Run {run_id}: analysing {nodes:,} entities ({engine}).", flush=True)
        result = analyse(conn, run_id, instance_id, engine, join_reviews)
    print(
        f"Run {run_id} done with {result['engine']}: {result['nodes']:,} entities,"
        f" {result['links']:,} links, {result['communities']:,} communities.",
        flush=True,
    )
    return result
