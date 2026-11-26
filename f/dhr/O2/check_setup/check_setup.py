# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#     "httpx==0.28.1",
#     "psycopg[binary]==3.3.9",
#     "wmill==1.875.0",
# ]
# ///
"""O2 stage 1: check everything a wiki build needs, before any GPU is used.

Checks, as the person running it: the chosen model is one the LLM gateway serves (its
catalogue, gateway/sessions.py MODELS; "default" is the gateway's default), the WeKnora key
works (the team space's, f/dhr/weknora_api_key), every knowledge-base setting is allowed
(f/dhr/O2/kb_settings.py), WeKnora's graph database is on when the knowledge graph is
wanted, and the chosen reviews are in the database, grouped by O1. It makes this person's
WeKnora entry for the chosen model if they have none yet (copied from their own entry, so
it carries their token: f/dhr/weknora.py). Starts nothing on Blythe.

Returns the WeKnora model id the later steps use, each review's size, how many reviews are
longer than WeKnora's wiki reads (it uses only the first 32,768 characters of each file;
each review is uploaded whole, as one file), and a rough count of the model calls the build
will make.
"""

from __future__ import annotations

from ...db import apply_migrations, connect
from ...llm import model_info
from ...weknora import WeKnora, WeKnoraError
from ..kb_settings import WEKNORA_WIKI_READ_LIMIT, check_settings
from ..reviews import check_choices, choose_reviews, review_file


def main(
    dataset: str = "DHRC-100",
    reviews: str = "pilot",
    doc_ids: list[str] | None = None,
    template_guidance: str = "dhr-2016",
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
    model: str = "default",
):
    check_choices(dataset, reviews, doc_ids, template_guidance)
    chosen_model = model_info(model)  # raises with the choices if the gateway lacks it
    kb = check_settings(
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
    entry = wk.model_entry(chosen_model)
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
            f = review_file(conn, review, template_guidance)
            sizes.append(
                {
                    "doc_id": review["doc_id"],
                    "dataset": review["dataset"],
                    "n_chars": f["n_chars"],
                    "wiki_reads_all": f["n_chars"] <= WEKNORA_WIKI_READ_LIMIT,
                    "n_joined": f["n_joined"],
                    "n_template_labels": f["n_template_labels"],
                }
            )
    total = sum(s["n_chars"] for s in sizes)
    n_files = len(sizes)
    chunks = round(total / max(1, kb["chunk_size"] - kb["chunk_overlap"]))
    return {
        "model_id": entry["id"],
        "model": chosen_model["id"],  # the gateway's key: what every later step asks for
        "model_name": chosen_model["model"],
        "weknora_entry": entry.get("display_name"),
        "graph_database": graph_database,
        "knowledge_base": kb,
        "n_reviews": len(chosen),
        "n_files": n_files,
        "total_chars": total,
        # WeKnora's wiki builder uses only the first 32,768 characters of these reviews.
        "n_longer_than_wiki_reads": sum(1 for s in sizes if not s["wiki_reads_all"]),
        # Rough: WeKnora's chunks are cut at sentence and heading ends, so counts vary.
        "estimate": {
            "chunks": chunks,
            "graph_calls": chunks if knowledge_graph else 0,
            "wiki_extraction_calls": n_files,
        },
        "reviews": sizes,
    }
