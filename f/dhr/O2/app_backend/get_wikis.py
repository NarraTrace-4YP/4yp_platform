# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#     "psycopg[binary]==3.3.9",
#     "wmill==1.875.0",
# ]
# ///
"""O2 stages 1-4, app: every wiki build, its reviews and its page counts (Wikis tab).

Read-only: lists wiki.instances newest first, each with how its reviews fared in WeKnora
(a review uploaded in parts is one row, its parts added up; review_status counts files)
and how many pages, links and page types its saved copy has. The instructions each build
was given are included, so a build can be read back exactly.
"""

from __future__ import annotations

from ...db import apply_migrations, connect

BUILDS_SQL = """
SELECT i.instance_id, i.name, i.created_by, i.created_at, i.finished_at, i.saved_at,
       i.status, i.status_note, i.dataset, i.reviews, i.model, i.settings, i.flow_job_id,
       i.git_commit, i.stats -> 'progress' AS progress,
       (SELECT count(DISTINCT doc_id) FROM wiki.documents d WHERE d.instance_id = i.instance_id) AS n_reviews,
       (SELECT count(*) FROM wiki.documents d WHERE d.instance_id = i.instance_id) AS n_files,
       (SELECT count(*) FROM wiki.pages p WHERE p.instance_id = i.instance_id) AS n_pages,
       (SELECT count(*) FROM wiki.links l WHERE l.instance_id = i.instance_id) AS n_links,
       (SELECT coalesce(jsonb_object_agg(page_type, n), '{}') FROM (
            SELECT page_type, count(*) AS n FROM wiki.pages p
            WHERE p.instance_id = i.instance_id GROUP BY page_type) t) AS pages_by_type,
       (SELECT coalesce(jsonb_object_agg(coalesce(parse_status, 'unknown'), n), '{}') FROM (
            SELECT parse_status, count(*) AS n FROM wiki.documents d
            WHERE d.instance_id = i.instance_id GROUP BY parse_status) t) AS review_status
FROM wiki.instances AS i
ORDER BY i.created_at DESC
"""
BUILD_COLUMNS = (
    "instance_id",
    "name",
    "created_by",
    "created_at",
    "finished_at",
    "saved_at",
    "status",
    "status_note",
    "dataset",
    "reviews",
    "model",
    "settings",
    "flow_job_id",
    "git_commit",
    "progress",
    "n_reviews",
    "n_files",
    "n_pages",
    "n_links",
    "pages_by_type",
    "review_status",
)
# One row per review, its parts added up: "completed" once every part is, "failed" if any
# part failed, otherwise how far its parts have got (e.g. "processing").
DOCUMENTS_SQL = """
SELECT d.instance_id, d.doc_id, min(d.dataset), sum(d.n_chars)::int, max(d.n_joined),
       CASE WHEN bool_or(d.parse_status = 'failed') THEN 'failed'
            WHEN bool_and(d.parse_status = 'completed') THEN 'completed'
            WHEN bool_and(d.parse_status IS NULL) THEN NULL
            ELSE coalesce(min(d.parse_status) FILTER (WHERE d.parse_status <> 'completed'), 'processing')
       END,
       string_agg(d.error_message, ' | ' ORDER BY d.part) FILTER (WHERE d.error_message IS NOT NULL),
       max(d.uploaded_at),
       (SELECT count(*) FROM wiki.page_sources s
        WHERE s.instance_id = d.instance_id AND s.doc_id = d.doc_id) AS n_pages
FROM wiki.documents AS d
GROUP BY d.instance_id, d.doc_id
ORDER BY d.instance_id, d.doc_id
"""
DOCUMENT_COLUMNS = (
    "doc_id",
    "dataset",
    "n_chars",
    "n_joined",
    "parse_status",
    "error_message",
    "uploaded_at",
    "n_pages",
)


def main():
    with connect() as conn:
        apply_migrations(conn)  # so the tables exist even before any build
        builds = [dict(zip(BUILD_COLUMNS, row)) for row in conn.execute(BUILDS_SQL)]
        documents: dict[str, list] = {}
        for row in conn.execute(DOCUMENTS_SQL):
            documents.setdefault(row[0], []).append(dict(zip(DOCUMENT_COLUMNS, row[1:])))
    for build in builds:
        build["documents"] = documents.get(build["instance_id"], [])
        for key in ("created_at", "finished_at", "saved_at"):
            build[key] = build[key].isoformat() if build[key] else None
        for d in build["documents"]:
            d["uploaded_at"] = d["uploaded_at"].isoformat() if d["uploaded_at"] else None
    return {"builds": builds}
