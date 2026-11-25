# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#     "psycopg[binary]==3.3.9",
#     "wmill==1.875.0",
# ]
# ///
"""O2 stages 1-4, app: one wiki page in full, with its links and sources (Pages tab).

Read-only. The page's text as WeKnora wrote it (Markdown with [[slug|title]] links), the
pages it links to and those that link to it, and the reviews it was built from.
"""

from __future__ import annotations

from ...db import apply_migrations, connect

PAGE_SQL = """
SELECT slug, title, page_type, status, summary, content, aliases, category_path,
       jsonb_array_length(chunk_refs), version, updated_at
FROM wiki.pages WHERE instance_id = %s AND slug = %s
"""
PAGE_COLUMNS = (
    "slug", "title", "page_type", "status", "summary", "content", "aliases", "category_path",
    "n_chunks_cited", "version", "updated_at",
)
OUT_SQL = """
SELECT l.target_slug, t.title, t.page_type
FROM wiki.links AS l
LEFT JOIN wiki.pages AS t ON t.instance_id = l.instance_id AND t.slug = l.target_slug
WHERE l.instance_id = %s AND l.source_slug = %s ORDER BY coalesce(t.title, l.target_slug)
"""
IN_SQL = """
SELECT l.source_slug, s.title, s.page_type
FROM wiki.links AS l JOIN wiki.pages AS s ON s.instance_id = l.instance_id AND s.slug = l.source_slug
WHERE l.instance_id = %s AND l.target_slug = %s ORDER BY s.title
"""
SOURCES_SQL = "SELECT doc_id FROM wiki.page_sources WHERE instance_id = %s AND slug = %s ORDER BY doc_id"


def main(instance_id: str, slug: str):
    with connect() as conn:
        apply_migrations(conn)
        row = conn.execute(PAGE_SQL, (instance_id, slug)).fetchone()
        if not row:
            return None
        page = dict(zip(PAGE_COLUMNS, row))
        page["updated_at"] = page["updated_at"].isoformat() if page["updated_at"] else None
        link = lambda r: {"slug": r[0], "title": r[1], "page_type": r[2]}  # noqa: E731
        page["links_out"] = [link(r) for r in conn.execute(OUT_SQL, (instance_id, slug))]
        page["links_in"] = [link(r) for r in conn.execute(IN_SQL, (instance_id, slug))]
        page["sources"] = [r[0] for r in conn.execute(SOURCES_SQL, (instance_id, slug))]
    return page
