# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#     "psycopg[binary]==3.3.9",
#     "wmill==1.875.0",
# ]
# ///
"""O2 stages 1-4, app: one wiki's pages and links, for its graph and page list (Graph, Pages tabs).

Read-only. Every page of the build's saved copy without its text (slug, title, type,
summary, how many links in and out, which reviews it came from), and every link between two
pages that exist. Links to pages that don't exist are counted, not returned.
"""

from __future__ import annotations

from ...db import apply_migrations, connect

PAGES_SQL = """
SELECT p.slug, p.title, p.page_type, left(coalesce(p.summary, ''), 400),
       (SELECT count(*) FROM wiki.links l
        WHERE l.instance_id = p.instance_id AND l.target_slug = p.slug) AS n_in,
       (SELECT count(*) FROM wiki.links l
        WHERE l.instance_id = p.instance_id AND l.source_slug = p.slug) AS n_out,
       coalesce((SELECT array_agg(s.doc_id ORDER BY s.doc_id) FROM wiki.page_sources s
                 WHERE s.instance_id = p.instance_id AND s.slug = p.slug), '{}') AS sources
FROM wiki.pages AS p
WHERE p.instance_id = %s
ORDER BY p.page_type, p.title
"""
PAGE_COLUMNS = ("slug", "title", "page_type", "summary", "n_in", "n_out", "sources")
LINKS_SQL = """
SELECT l.source_slug, l.target_slug
FROM wiki.links AS l
JOIN wiki.pages AS t ON t.instance_id = l.instance_id AND t.slug = l.target_slug
WHERE l.instance_id = %s
"""
BROKEN_SQL = """
SELECT count(*) FROM wiki.links AS l
WHERE l.instance_id = %s
  AND NOT EXISTS (SELECT 1 FROM wiki.pages t WHERE t.instance_id = l.instance_id AND t.slug = l.target_slug)
"""


def main(instance_id: str):
    with connect() as conn:
        apply_migrations(conn)
        pages = [dict(zip(PAGE_COLUMNS, row)) for row in conn.execute(PAGES_SQL, (instance_id,))]
        links = [{"source": s, "target": t} for s, t in conn.execute(LINKS_SQL, (instance_id,))]
        broken = conn.execute(BROKEN_SQL, (instance_id,)).fetchone()[0]
    return {"instance_id": instance_id, "pages": pages, "links": links, "broken_links": broken}
