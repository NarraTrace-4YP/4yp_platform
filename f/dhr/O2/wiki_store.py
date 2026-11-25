"""O2 stages 1-4, shared: a record of each wiki build in the database (schema wiki).

WeKnora holds the live wiki. save_wiki() copies every page, its links and the reviews it
was built from into wiki.pages / wiki.links / wiki.page_sources, replacing the build's
previous copy in one transaction, so the O2 app and later analysis read a consistent
snapshot that stays put if WeKnora's copy changes or is deleted.
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timezone


def new_instance_id(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return f"wiki-{now:%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"


def runner() -> str:
    return os.environ.get("WM_USERNAME") or os.environ.get("USER") or "local"


def flow_job_id() -> str | None:
    return os.environ.get("WM_ROOT_FLOW_JOB_ID") or os.environ.get("WM_FLOW_JOB_ID") or os.environ.get("WM_JOB_ID")


def set_status(conn, instance_id: str, status: str, note: str | None = None, finished: bool = False):
    conn.execute(
        """
        UPDATE wiki.instances
        SET status = %s, status_note = %s,
            finished_at = CASE WHEN %s THEN now() ELSE finished_at END
        WHERE instance_id = %s
        """,
        (status, note, finished, instance_id),
    )


def get_instance(conn, instance_id: str) -> dict:
    row = conn.execute(
        """
        SELECT instance_id, name, created_by, dataset, reviews, model, weknora_kb_id,
               settings, status
        FROM wiki.instances WHERE instance_id = %s
        """,
        (instance_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"No wiki build {instance_id!r} in wiki.instances")
    keys = ("instance_id", "name", "created_by", "dataset", "reviews", "model", "kb_id", "settings", "status")
    return dict(zip(keys, row))


def doc_ids_by_knowledge(conn, instance_id: str) -> dict[str, str]:
    return dict(
        conn.execute(
            "SELECT knowledge_id, doc_id FROM wiki.documents"
            " WHERE instance_id = %s AND knowledge_id IS NOT NULL",
            (instance_id,),
        ).fetchall()
    )


def _source_doc_ids(page: dict, by_knowledge: dict[str, str], by_file: dict[str, str]) -> set[str]:
    """source_refs are "<knowledge_id>|<document title>" (title = the uploaded file name,
    sometimes without .md)."""
    found = set()
    for ref in page.get("source_refs") or []:
        knowledge_id, _, title = str(ref).partition("|")
        doc_id = by_knowledge.get(knowledge_id) or by_file.get(title) or by_file.get(f"{title}.md")
        if doc_id:
            found.add(doc_id)
    return found


def save_wiki(conn, instance_id: str, pages: list[dict], stats: dict | None) -> dict:
    """Replaces the build's copy of its pages, links and page sources. Returns counts."""
    by_knowledge = doc_ids_by_knowledge(conn, instance_id)
    by_file = dict(
        conn.execute(
            "SELECT file_name, doc_id FROM wiki.documents WHERE instance_id = %s", (instance_id,)
        ).fetchall()
    )
    slugs = {p["slug"] for p in pages if p.get("slug")}
    page_rows, link_rows, source_rows = [], set(), set()
    for p in pages:
        slug = p.get("slug")
        if not slug:
            continue
        page_rows.append(
            (
                instance_id,
                slug,
                p.get("title") or slug,
                p.get("page_type") or "page",
                p.get("status"),
                p.get("summary"),
                p.get("content") or "",
                json.dumps(p.get("aliases") or []),
                json.dumps(p.get("category_path") or []),
                json.dumps(p.get("chunk_refs") or []),
                p.get("version"),
                p.get("created_at"),
                p.get("updated_at"),
            )
        )
        for target in p.get("out_links") or []:
            if target and target != slug:
                link_rows.add((instance_id, slug, target))
        for doc_id in _source_doc_ids(p, by_knowledge, by_file):
            source_rows.add((instance_id, slug, doc_id))
    with conn.transaction():
        conn.execute("DELETE FROM wiki.pages WHERE instance_id = %s", (instance_id,))
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO wiki.pages (instance_id, slug, title, page_type, status, summary,
                    content, aliases, category_path, chunk_refs, version, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                page_rows,
            )
            cur.executemany(
                "INSERT INTO wiki.links (instance_id, source_slug, target_slug) VALUES (%s, %s, %s)",
                sorted(link_rows),
            )
            cur.executemany(
                "INSERT INTO wiki.page_sources (instance_id, slug, doc_id) VALUES (%s, %s, %s)",
                sorted(source_rows),
            )
        conn.execute(
            "UPDATE wiki.instances SET saved_at = now(), stats = %s WHERE instance_id = %s",
            (json.dumps(stats or {}, default=str), instance_id),
        )
    return {
        "pages": len(page_rows),
        "links": len(link_rows),
        "broken_links": sum(1 for _, _, t in link_rows if t not in slugs),
        "page_sources": len(source_rows),
    }
