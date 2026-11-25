# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#     "httpx==0.28.1",
#     "psycopg[binary]==3.3.9",
#     "wmill==1.875.0",
# ]
# ///
"""O2 stage 4: check how a wiki build is coming on, and save a copy of the wiki so far.

Asks WeKnora how far each uploaded review has got (read, failed, still waiting) and whether
its wiki builder still has work queued, records that in wiki.documents, and copies every
wiki page, link and page source into the database (so the O2 app shows the wiki as it
grows). It also reports live progress (f/dhr/O2/progress.py): a line in the run's log with
the reviews read, the wiki's queue, the pages so far and what the model is doing (from the
LLM gateway), the run's progress bar, and the same line in the O2 app.

The flow runs this every few minutes until the build has been quiet (every review finished,
nothing queued, builder idle) on `quiet_needed` checks in a row, then marks the build
"built". Safe to run on its own at any time for any build you made.
"""

from __future__ import annotations

from ...db import apply_migrations, connect
from ...llm import describe_gateway, gateway_status
from ...weknora import WeKnora
from ..progress import FINISHED, build_progress, report
from ..wiki_store import get_instance, runner, save_wiki, set_status


def main(instance_id: str, quiet_before: int = 0, quiet_needed: int = 2):
    wk = WeKnora()
    with connect() as conn:
        apply_migrations(conn)
        build = get_instance(conn, instance_id)
        # Checking never calls the model, so in the shared space anyone may check any build.
        if not wk.shared and build["created_by"] != runner():
            raise ValueError(
                f"{instance_id} was made by {build['created_by']}: its wiki is in their WeKnora space."
            )
        kb_id = build["kb_id"]
        documents = wk.documents(kb_id)
        by_id = {d.get("id"): d for d in documents}
        # One row per uploaded file: a review in parts has several (f/dhr/O2/kb_settings.py).
        # A file is "completed" only once WeKnora has also finished its knowledge-graph
        # calls, so the quiet checks below wait for the graph too.
        expected = conn.execute(
            "SELECT doc_id, part, n_parts, knowledge_id FROM wiki.documents"
            " WHERE instance_id = %s",
            (instance_id,),
        ).fetchall()
        counts: dict[str, int] = {}
        failed = []
        for doc_id, part, n_parts, knowledge_id in expected:
            d = by_id.get(knowledge_id)
            status = d.get("parse_status") if d else ("missing" if knowledge_id else "not uploaded")
            counts[status] = counts.get(status, 0) + 1
            if status == "failed":
                failed.append({"doc_id": doc_id, "part": f"{part} of {n_parts}",
                               "error": (d or {}).get("error_message")})
            conn.execute(
                "UPDATE wiki.documents SET parse_status = %s,"
                " error_message = COALESCE(%s, error_message)"
                " WHERE instance_id = %s AND doc_id = %s AND part = %s",
                (status, (d or {}).get("error_message") or None, instance_id, doc_id, part),
            )
        stats = wk.wiki_stats(kb_id) or {}
        pages = wk.wiki_pages(kb_id)
        n_links = sum(len(p.get("out_links") or []) for p in pages)
        max_pending = conn.execute(
            "SELECT coalesce((stats -> 'progress' ->> 'max_pending')::int, 0)"
            " FROM wiki.instances WHERE instance_id = %s",
            (instance_id,),
        ).fetchone()[0]
        progress = build_progress(
            counts, stats, len(pages), n_links, describe_gateway(gateway_status()), max_pending
        )
        saved = save_wiki(conn, instance_id, pages, {**stats, "progress": progress})

        all_finished = all(s in FINISHED for s in counts) and bool(expected)
        idle = not stats.get("is_active") and not stats.get("pending_tasks")
        quiet = quiet_before + 1 if all_finished and idle else 0
        done = quiet >= quiet_needed
        if done:
            note = f"{len(failed)} file(s) failed in WeKnora" if failed else None
            set_status(conn, instance_id, "built", note, finished=True)
        else:
            set_status(conn, instance_id, "building", None)
    report(progress, 99 if done else None)
    if done:
        print(f"The build is finished: {quiet_needed} quiet checks in a row.", flush=True)
    return {
        "instance_id": instance_id,
        "done": done,
        "quiet": quiet,
        "documents": counts,
        "failed": failed,
        "wiki_pending_tasks": stats.get("pending_tasks"),
        "wiki_active": stats.get("is_active"),
        "pages_by_type": stats.get("pages_by_type"),
        "saved": saved,
        "progress": progress["line"],
        "percent": 100 if done else progress["percent"],
    }
