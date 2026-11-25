# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#     "httpx==0.28.1",
#     "psycopg[binary]==3.3.9",
#     "wmill==1.875.0",
# ]
# ///
"""O2 stage 3: make a new wiki (a WeKnora knowledge base) and queue the reviews into it.

Fix (25 Nov 2026, problem review "O2 wiki reads only the start of each review"): WeKnora
v0.9.0's wiki builder reads only the first 32,768 characters of each file, and 97 of the 100
DHRC-100 reviews are longer, so about 75% of the text never reached the wiki. Why: a fixed
limit inside WeKnora (maxContentForWiki). Solution: each review is uploaded as parts under
part_chars characters (default 30,000), cut at headings (f/dhr/O2/reviews.py), one
wiki.documents row per part. At the same time (Rowan's request), every knowledge-base setting
that can change safely is now an input: chunking, the wiki's throughput settings and the
knowledge graph, on by default (f/dhr/O2/kb_settings.py lists them, and the ones that stay
fixed, with reasons).

Records a new build in wiki.instances, creates a knowledge base in the team's WeKnora space
(wiki on, knowledge graph on unless turned off, vector and keyword search off) with the
runner's own entry for the team model and the DHR instructions (f/dhr/O2/instructions.py,
or the text given here), then uploads each chosen review as Markdown, in parts
(f/dhr/O2/reviews.py), and records exactly what was uploaded in wiki.documents. WeKnora
reads each file and builds the wiki (and the graph) in the background.

To finish a build whose uploads stopped part-way, give its instance_id: nothing new is made,
and only the files not yet uploaded are sent, cut the way the build was first cut.
"""

from __future__ import annotations

import json

from ...db import apply_migrations, connect
from ...llm import MODEL
from ...version import GIT_COMMIT
from ...weknora import WeKnora, WeKnoraError
from ..instructions import CONTENT, EXTRACTION, GRANULARITIES
from ..kb_settings import check_settings, chunking_config, graph_config, wiki_tuning
from ..progress import report_upload
from ..reviews import check_choices, choose_reviews, review_parts
from ..wiki_store import flow_job_id, get_instance, new_instance_id, runner, set_status


def main(
    name: str = "",
    dataset: str = "DHRC-100",
    reviews: str = "pilot",
    doc_ids: list[str] | None = None,
    template_guidance: str = "dhr-2016",
    granularity: str = "standard",
    extraction_instructions: str = "",
    content_instructions: str = "",
    model_id: str = "",
    instance_id: str = "",
    part_chars: int = 30000,  # kb_settings.DEFAULT_PART_CHARS
    chunk_strategy: str = "auto",
    chunk_size: int = 512,
    chunk_overlap: int = 80,
    chunk_separators: list[str] | None = None,
    ingest_batch_size: int = 0,
    ingest_map_parallel: int = 0,
    ingest_reduce_parallel: int = 0,
    ingest_max_inflight: int = 0,
    knowledge_graph: bool = True,
    graph_instructions: str = "",
):
    check_choices(dataset, reviews, doc_ids, template_guidance)
    kb = check_settings(
        part_chars,
        chunk_strategy,
        chunk_size,
        chunk_overlap,
        chunk_separators,
        ingest_batch_size,
        ingest_map_parallel,
        ingest_reduce_parallel,
        ingest_max_inflight,
        knowledge_graph,
        graph_instructions,
    )
    if granularity not in GRANULARITIES:
        raise ValueError(f"granularity must be one of {', '.join(GRANULARITIES)}")
    extraction = extraction_instructions.strip() or EXTRACTION
    content = content_instructions.strip() or CONTENT
    for label, text in (("extraction", extraction), ("content", content)):
        if len(text.encode("utf-8")) > 4000:
            raise ValueError(f"The {label} instructions are over WeKnora's 4,000-byte limit")
    wk = WeKnora()

    with connect() as conn:
        apply_migrations(conn)
        if instance_id:
            build = get_instance(conn, instance_id)
            if build["created_by"] != runner():
                raise ValueError(
                    f"{instance_id} was made by {build['created_by']}: its knowledge base is in"
                    " their WeKnora space, so only they can add to it."
                )
            settings = build["settings"]
            dataset, reviews = build["dataset"], build["reviews"]
            doc_ids = settings.get("doc_ids")
            template_guidance = settings.get("template_guidance", "none")
            # A build made before parts existed uploaded each review whole.
            kb = settings.get("knowledge_base") or {**kb, "part_chars": 0}
            kb_id = build["kb_id"]
        else:
            model_id = model_id or wk.chat_model(MODEL)["id"]
            instance_id = new_instance_id()
            name = name.strip() or f"{dataset} {reviews} ({instance_id})"
            settings = {
                "granularity": granularity,
                "template_guidance": template_guidance,
                "extraction_instructions": extraction,
                "content_instructions": content,
                "weknora_model_id": model_id,
                "doc_ids": doc_ids if reviews == "chosen" else None,
                "knowledge_base": kb,
            }
            # Fails before anything is made if a review is missing.
            chosen = choose_reviews(conn, dataset, reviews, doc_ids)
            conn.execute(
                """
                INSERT INTO wiki.instances (instance_id, name, created_by, flow_job_id, git_commit,
                    dataset, reviews, model, settings)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    instance_id,
                    name,
                    runner(),
                    flow_job_id(),
                    GIT_COMMIT,
                    dataset,
                    reviews,
                    MODEL,
                    json.dumps(settings),
                ),
            )
            try:
                made = wk.create_knowledge_base(
                    name=name,
                    description=(
                        f"NarraTrace O2 wiki {instance_id}: {len(chosen)} reviews from {dataset}"
                        f" ({reviews}). Built from Windmill (f/dhr/O2/pipeline)."
                    ),
                    chat_model_id=model_id,
                    granularity=granularity,
                    extraction_instructions=extraction,
                    content_instructions=content,
                    chunking_config=chunking_config(kb),
                    wiki_tuning=wiki_tuning(kb),
                    extract_config=graph_config(kb),
                )
            except WeKnoraError as error:
                set_status(conn, instance_id, "failed", str(error), finished=True)
                raise
            kb_id = made["id"]
            conn.execute(
                "UPDATE wiki.instances SET weknora_kb_id = %s WHERE instance_id = %s",
                (kb_id, instance_id),
            )

        set_status(conn, instance_id, "uploading")
        chosen = choose_reviews(conn, dataset, reviews, doc_ids)
        done = {
            (r[0], r[1], r[2])
            for r in conn.execute(
                "SELECT doc_id, part, n_parts FROM wiki.documents"
                " WHERE instance_id = %s AND knowledge_id IS NOT NULL",
                (instance_id,),
            ).fetchall()
        }
        uploaded, failed = [], []
        n_files = 0
        for number, review in enumerate(chosen, start=1):
            files = review_parts(conn, review, template_guidance, kb["part_chars"])
            n_files += len(files)
            if all((f["doc_id"], f["part"], f["n_parts"]) in done for f in files):
                continue
            # A rerun cuts a review the same way unless the review changed in the meantime;
            # then its old rows no longer match a file and are replaced.
            conn.execute(
                "DELETE FROM wiki.documents WHERE instance_id = %s AND doc_id = %s"
                " AND (n_parts <> %s OR knowledge_id IS NULL)",
                (instance_id, review["doc_id"], len(files)),
            )
            for f in files:
                key = (f["doc_id"], f["part"], f["n_parts"])
                if key in done:
                    continue
                conn.execute(
                    """
                    INSERT INTO wiki.documents (instance_id, doc_id, part, n_parts, dataset,
                        revision, file_name, sha256, n_chars, n_joined)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (instance_id, doc_id, part) DO UPDATE SET
                        n_parts = EXCLUDED.n_parts, dataset = EXCLUDED.dataset,
                        revision = EXCLUDED.revision, file_name = EXCLUDED.file_name,
                        sha256 = EXCLUDED.sha256, n_chars = EXCLUDED.n_chars,
                        n_joined = EXCLUDED.n_joined, error_message = NULL
                    """,
                    (
                        instance_id,
                        f["doc_id"],
                        f["part"],
                        f["n_parts"],
                        f["dataset"],
                        f["revision"],
                        f["file_name"],
                        f["sha256"],
                        f["n_chars"],
                        f["n_joined"],
                    ),
                )
                try:
                    knowledge = wk.upload_markdown(kb_id, f["file_name"], f["text"])
                except WeKnoraError as error:
                    conn.execute(
                        "UPDATE wiki.documents SET error_message = %s"
                        " WHERE instance_id = %s AND doc_id = %s AND part = %s",
                        (str(error)[:2000], instance_id, f["doc_id"], f["part"]),
                    )
                    failed.append(
                        {"doc_id": f["doc_id"], "part": f["part"], "error": str(error)[:300]}
                    )
                    continue
                conn.execute(
                    """
                    UPDATE wiki.documents SET knowledge_id = %s, parse_status = %s, uploaded_at = now()
                    WHERE instance_id = %s AND doc_id = %s AND part = %s
                    """,
                    (
                        knowledge.get("id"),
                        knowledge.get("parse_status"),
                        instance_id,
                        f["doc_id"],
                        f["part"],
                    ),
                )
                uploaded.append(key)
            report_upload(
                number,
                len(chosen),
                f"{review['doc_id']} ({len(files)} part{'s' if len(files) != 1 else ''})",
            )

        if failed and not uploaded and not done:
            set_status(
                conn,
                instance_id,
                "failed",
                f"no file could be uploaded: {failed[0]['error']}",
                True,
            )
            raise WeKnoraError(f"No file could be uploaded: {failed[0]['error']}")
        note = (
            f"{len(failed)} file(s) failed to upload; rerun with this instance_id"
            if failed
            else None
        )
        set_status(conn, instance_id, "building", note)
    return {
        "instance_id": instance_id,
        "kb_id": kb_id,
        "name": name,
        "reviews": len(chosen),
        "files": n_files,
        "uploaded": len(uploaded),
        "already_uploaded": len(done),
        "failed": failed,
        "knowledge_base": kb,
    }
