# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#     "httpx==0.28.1",
#     "neo4j==5.28.8",
#     "paramiko==5.1.0",
#     "psycopg[binary]==3.3.9",
#     "pyotp==2.12.1",
#     "wmill==1.875.0",
# ]
# ///
"""O2: delete a wiki build completely.

Fix (25 Nov 2026, problem review "O2 wikis can't be deleted"): Problem: there was no way to
remove a wiki build. Deleting it in WeKnora's own pages left its saved copy in the database
(so the O2 app kept showing it) and the Windmill flow still checking it; nothing removed
the database copy at all. Why: the pipeline only ever made builds. Solution: this script
removes everything a build made, in this order, and can be run again if a step fails:
  1. stops the build's pipeline run, if it is still going (so it can't save the wiki again);
  2. deletes the knowledge base in WeKnora: its files, chunks, wiki pages and knowledge
     graph (WeKnora stops its queued work for it at once and clears the rest in the
     background);
  3. removes the build's entities from WeKnora's graph database (weknora-neo4j) as well,
     in case WeKnora's background clean-up doesn't finish;
  4. tidies up the model on Blythe: asks the person's LLM job (the one the gateway started
     for them, gw-...) to stop once it has answered what it was already given, so the GPU
     is freed now instead of after 30 idle minutes. It is left running when another of
     their builds is still uploading or building, or with stop_model_job off. The job is shared
     by everything the person sends through the gateway; the next call simply starts a new
     one. Logs in to Blythe as the person running this, with their own variables;
  5. deletes the build from the database: wiki.instances and, with it, its documents,
     pages, links, page sources and knowledge-graph copy (they all cascade).
The database copy goes last, so if WeKnora can't be reached nothing is lost track of: run
it again later.

Only the person who made a build, or a Windmill workspace admin, can delete it (it is their
work). An admin deleting someone else's build leaves that person's model job alone: it can
only be reached with their own Blythe login, and it stops by itself after 30 idle minutes. To make sure the right build goes, give its
instance_id twice: as instance_id and as confirm. This is a Windmill script only; the
public O2 app can't delete anything.

Rowan, 25 Nov 2026: deleting a wiki also tidies up the SLURM LLM job (step 4).
"""

from __future__ import annotations

from ...blythe import Blythe
from ...db import apply_migrations, connect
from ...llm import gateway_status
from ...weknora import WeKnora
from ..kgraph import GraphUnavailable, delete_graph
from ..wiki_store import get_instance, runner

COUNTS_SQL = """
SELECT (SELECT count(*) FROM wiki.documents WHERE instance_id = %(i)s),
       (SELECT count(*) FROM wiki.pages WHERE instance_id = %(i)s),
       (SELECT count(*) FROM wiki.kg_nodes WHERE instance_id = %(i)s)
"""


def stop_flow(job_id: str | None) -> str:
    """Cancels the build's pipeline run if it is still going; says what happened."""
    if not job_id:
        return "no pipeline run recorded"
    try:
        import wmill

        status = wmill.get_job_status(job_id)
        if status == "COMPLETED":
            return "the pipeline run had already finished"
        wmill.cancel_job(job_id, "the wiki build is being deleted (f/dhr/O2/delete_wiki)")
        return f"cancelled the pipeline run {job_id}"
    except Exception as error:  # noqa: BLE001 - the run may be gone; carry on regardless
        return f"couldn't check the pipeline run {job_id} ({str(error)[:200]})"


def is_admin() -> bool:
    """Whether the person running this is an admin of the Windmill workspace."""
    try:
        import wmill

        client = wmill.Windmill()
        return bool(client.get(f"/w/{client.workspace}/users/whoami").json().get("is_admin"))
    except Exception:  # noqa: BLE001 - not known: not an admin
        return False


def stop_model(other_builds: list[str]) -> str:
    """Asks the person's gateway LLM job to stop once its inbox is empty; says what happened."""
    if other_builds:
        return (
            f"left your model job running: your other build(s) {', '.join(other_builds[:3])}"
            " still use it (it stops by itself after 30 idle minutes)"
        )
    status = gateway_status()
    if status.get("error"):
        return f"{status['error']}; your model job stops by itself after 30 idle minutes"
    run_id = status.get("run_id")
    if not run_id:
        return "no model job of yours was running"
    if status.get("job_state") in ("stopped", "failed"):
        return f"your model job {run_id} had already ended"
    try:
        with Blythe() as b:
            b.stop_server(run_id)
    except Exception as error:  # noqa: BLE001 - the wiki is still deleted; the job idles out
        return (
            f"couldn't ask your model job {run_id} to stop ({str(error)[:200]});"
            " it stops by itself after 30 idle minutes"
        )
    job = status.get("slurm_job_id")
    return (
        f"asked your model job {run_id}{f' (SLURM job {job})' if job else ''} to stop once it"
        " has answered what it was already given"
    )


def main(instance_id: str, confirm: str = "", stop_model_job: bool = True):
    instance_id = instance_id.strip()
    if not instance_id or confirm.strip() != instance_id:
        raise ValueError(
            "To delete a build, give its instance_id (e.g. wiki-20261124-221500-ab12) as both"
            " instance_id and confirm. Nothing was deleted."
        )
    with connect() as conn:
        apply_migrations(conn)
        build = get_instance(conn, instance_id)
        own = build["created_by"] == runner()
        if not own and not is_admin():
            raise ValueError(
                f"{instance_id} was made by {build['created_by']}: only they (or a workspace"
                " admin) can delete it. Nothing was deleted."
            )
        flow_job = conn.execute(
            "SELECT flow_job_id FROM wiki.instances WHERE instance_id = %s", (instance_id,)
        ).fetchone()[0]
        n_documents, n_pages, n_entities = conn.execute(COUNTS_SQL, {"i": instance_id}).fetchone()
        other_builds = [
            row[0]
            for row in conn.execute(
                "SELECT instance_id FROM wiki.instances WHERE created_by = %s"
                " AND instance_id <> %s AND status IN ('uploading', 'building')",
                (build["created_by"], instance_id),
            ).fetchall()
        ]

    flow = stop_flow(flow_job)
    kb_id = build["kb_id"]
    if kb_id:
        weknora = (
            "deleted the knowledge base"
            if WeKnora().delete_knowledge_base(kb_id)
            else "the knowledge base was already gone"
        )
        try:
            graph = f"removed {delete_graph(kb_id)} entities from the graph database"
        except GraphUnavailable as error:
            graph = (
                f"couldn't reach the graph database ({error}); WeKnora's own clean-up removes them"
            )
    else:
        weknora = "no knowledge base had been made"
        graph = "nothing to remove"
    if not own:
        model = (
            f"left {build['created_by']}'s model job alone (only their own login reaches it;"
            " it stops by itself after 30 idle minutes)"
        )
    elif stop_model_job:
        model = stop_model(other_builds)
    else:
        model = "left your model job running"

    with connect() as conn:
        conn.execute("DELETE FROM wiki.instances WHERE instance_id = %s", (instance_id,))
    print(
        f"Deleted {instance_id} ({build['name']}): {weknora}; {graph}; {flow}; {model}", flush=True
    )
    return {
        "instance_id": instance_id,
        "name": build["name"],
        "pipeline": flow,
        "weknora": weknora,
        "graph_database": graph,
        "model_job": model,
        "database": {
            "documents": n_documents,
            "pages": n_pages,
            "knowledge_graph_entities": n_entities,
        },
    }
