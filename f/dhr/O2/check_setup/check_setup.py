# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#     "httpx==0.28.1",
#     "psycopg[binary]==3.3.9",
#     "wmill==1.875.0",
# ]
# ///
"""O2 stage 1: check everything a wiki build needs, before any GPU is used.

Checks, as the person running it: the WeKnora key works (the team space's, f/dhr/
weknora_api_key), the space has this person's entry for the team model, every
knowledge-base setting is allowed (f/dhr/O2/kb_settings.py), WeKnora's graph database is on
when the knowledge graph is wanted, and the chosen reviews are in the database, grouped by
O1. Starts nothing on Blythe and changes nothing in WeKnora, so it is safe to run on its own.

Returns the WeKnora model id the later steps use, each review's size and how many files
(parts) it will be uploaded as, and a rough count of the model calls the build will make.
"""

from __future__ import annotations

from ...db import apply_migrations, connect
from ...llm import MODEL
from ...weknora import WeKnora, WeKnoraError
from ..kb_settings import check_settings
from ..reviews import check_choices, choose_reviews, review_parts


def main(
    dataset: str = "DHRC-100",
    reviews: str = "pilot",
    doc_ids: list[str] | None = None,
    template_guidance: str = "dhr-2016",
    part_chars: int = 30000,
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
    wk = WeKnora()
    model = wk.chat_model(MODEL)
    graph_database = wk.graph_database()
    if knowledge_graph and graph_database.lower() != "neo4j":
        raise WeKnoraError(
            f"WeKnora's graph database is {graph_database!r}, so it can't build the knowledge"
            ' graph. Turn on weknora-neo4j on the server (docs/weknora.md, "Knowledge graph"),'
            " or run with knowledge_graph off."
        )
    with connect() as conn:
        apply_migrations(conn)
        chosen = choose_reviews(conn, dataset, reviews, doc_ids)
        sizes = []
        for review in chosen:
            files = review_parts(conn, review, template_guidance, kb["part_chars"])
            sizes.append(
                {
                    "doc_id": review["doc_id"],
                    "dataset": review["dataset"],
                    "n_chars": sum(f["n_chars"] for f in files),
                    "n_parts": len(files),
                    "n_joined": files[0]["n_joined"],
                    "n_template_labels": files[0]["n_template_labels"],
                }
            )
    total = sum(s["n_chars"] for s in sizes)
    n_files = sum(s["n_parts"] for s in sizes)
    chunks = round(total / max(1, kb["chunk_size"] - kb["chunk_overlap"]))
    return {
        "model_id": model["id"],
        "model": MODEL,
        "graph_database": graph_database,
        "knowledge_base": kb,
        "n_reviews": len(chosen),
        "n_files": n_files,
        "total_chars": total,
        # Rough: WeKnora's chunks are cut at sentence and heading ends, so counts vary.
        "estimate": {
            "chunks": chunks,
            "graph_calls": chunks if knowledge_graph else 0,
            "wiki_extraction_calls": n_files,
        },
        "reviews": sizes,
    }
