# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#     "psycopg[binary]==3.3.9",
#     "wmill==1.875.0",
# ]
# ///
"""Read template candidates and their scores for the O1 app.

Return the latest matching run for the requested dataset and guidance, attach
candidate lists to review sections, and report coverage for each template.
Candidate lists remain separate from any future final template assignment.
"""

from typing import Literal

from ...datasets import check_dataset
from ...db import apply_migrations, connect
from .templates import GUIDANCES

MAX_MATCHES = 500

RUN_SQL = """
SELECT c.revision, c.model, c.k, c.min_heading_score, c.matched_at, c.git_commit,
       c.n_sections, c.n_candidates
FROM corpus.current_versions AS v
JOIN corpus.candidate_versions AS c USING (revision)
WHERE v.dataset = %s AND c.guidance = %s
"""

GUIDANCE_SQL = "SELECT title, source_url FROM corpus.guidance_documents WHERE guidance = %s"

TEMPLATES_SQL = """
SELECT t.template, t.appendix, t.title, t.kind, t.source_url,
       ts.position, ts.heading, ts.printed, ts.guidance, ts.page
FROM corpus.templates AS t
JOIN corpus.template_sections AS ts USING (template)
WHERE t.guidance = %s
ORDER BY t.appendix, ts.position
"""

# Every candidate of the guidance's templates, marked if it is its section's best match by
# text or by heading. {only} narrows it to some sections (the window needs all of a section's
# candidates, so it can't be narrowed to one template section).
SCORED_SQL = """
SELECT c.doc_id, c.section_id, c.template, c.template_position,
       c.heading_score, c.heading_rank, c.text_score, c.text_rank,
       c.text_score >= max(c.text_score) OVER w - 0.0001 AS best_by_text,
       c.heading_score >= %(min)s
           AND c.heading_score >= max(c.heading_score) OVER w - 0.0001 AS best_by_heading
FROM corpus.template_candidates AS c
JOIN corpus.templates AS t USING (template)
WHERE c.revision = %(revision)s AND t.guidance = %(guidance)s {only}
WINDOW w AS (PARTITION BY c.doc_id, c.section_id)
"""
SCORED = SCORED_SQL.format(only="")

# For each template section, how many review sections have it as their best match.
COUNTS_SQL = f"""
WITH scored AS ({SCORED})
SELECT template, template_position,
       count(*) FILTER (WHERE best_by_text),
       count(*) FILTER (WHERE best_by_heading),
       count(*) FILTER (WHERE best_by_text AND best_by_heading)
FROM scored
GROUP BY template, template_position
"""

# For each review: its sections, how many have a best match by heading, and how many have
# a template section that is their best match by both heading and text.
REVIEWS_SQL = f"""
WITH scored AS ({SCORED})
SELECT doc_id, count(DISTINCT section_id),
       count(DISTINCT section_id) FILTER (WHERE best_by_heading),
       count(DISTINCT section_id) FILTER (WHERE best_by_text AND best_by_heading)
FROM scored
GROUP BY doc_id
ORDER BY doc_id
"""
REVIEW_COLUMNS = ("doc_id", "n_sections", "n_heading_match", "n_agree")

SECTIONS_SQL = """
SELECT section_id, parent_id, level, heading, position, first_page, last_page
FROM corpus.sections
WHERE revision = %s AND doc_id = %s
ORDER BY position
"""
SECTION_COLUMNS = (
    "section_id",
    "parent_id",
    "level",
    "heading",
    "position",
    "first_page",
    "last_page",
)

CANDIDATES_SQL = """
SELECT c.section_id, c.template, c.template_position, ts.heading,
       c.heading_score, c.heading_rank, c.text_score, c.text_rank
FROM corpus.template_candidates AS c
JOIN corpus.templates AS t USING (template)
JOIN corpus.template_sections AS ts
  ON ts.template = c.template AND ts.position = c.template_position
WHERE c.revision = %(revision)s AND c.doc_id = %(doc_id)s AND t.guidance = %(guidance)s
ORDER BY c.text_rank, c.heading_rank
"""
CANDIDATE_COLUMNS = (
    "section_id",
    "template",
    "template_position",
    "template_heading",
    "heading_score",
    "heading_rank",
    "text_score",
    "text_rank",
)

# The review sections whose best match (by text, or by heading) is one template section.
MATCHES_SCORED = SCORED_SQL.format(
    only="""AND (c.doc_id, c.section_id) IN (
        SELECT doc_id, section_id FROM corpus.template_candidates
        WHERE revision = %(revision)s AND template = %(template)s
          AND template_position = %(position)s)"""
)
MATCHES_WHERE = """
WHERE m.template = %(template)s AND m.template_position = %(position)s
  AND (m.best_by_text OR m.best_by_heading)
"""
MATCHES_SQL = f"""
WITH m AS ({MATCHES_SCORED})
SELECT m.doc_id, m.section_id, s.level, s.heading, s.position, m.heading_score,
       m.heading_rank, m.text_score, m.text_rank, m.best_by_text, m.best_by_heading
FROM m
JOIN corpus.sections AS s
  ON s.revision = %(revision)s AND s.doc_id = m.doc_id AND s.section_id = m.section_id
{MATCHES_WHERE}
ORDER BY (m.best_by_text AND m.best_by_heading) DESC, m.heading_score DESC,
         m.text_score DESC, m.doc_id, s.position
LIMIT %(limit)s
"""
MATCHES_COUNT_SQL = f"WITH m AS ({MATCHES_SCORED}) SELECT count(*) FROM m {MATCHES_WHERE}"
MATCH_COLUMNS = (
    "doc_id",
    "section_id",
    "level",
    "heading",
    "position",
    "heading_score",
    "heading_rank",
    "text_score",
    "text_rank",
    "best_by_text",
    "best_by_heading",
)
TIE = 0.0001  # scores this close are equal (they are stored as 4-byte floats)


def as_dicts(rows, columns) -> list[dict]:
    """Map database columns to row values, rejecting mismatched row lengths."""
    return [dict(zip(columns, row, strict=True)) for row in rows]


def found_by(candidate: dict, k: int, min_heading_score: float) -> str:
    """How a candidate was found: "both", "text" or "heading" (see select_candidates:
    the k best by text, and the k best by heading if they score at least min_heading_score).
    """
    by_text = candidate["text_rank"] <= k
    by_heading = candidate["heading_rank"] <= k and candidate["heading_score"] >= min_heading_score
    return "both" if by_text and by_heading else "text" if by_text else "heading"


def attach_candidates(sections, candidates, k: int, min_heading_score: float) -> list[dict]:
    """Attach ranked candidates to sections and mark the best scores within tie tolerance."""
    result = as_dicts(sections, SECTION_COLUMNS)
    by_section = {s["section_id"]: s for s in result}
    for s in result:
        s["candidates"] = []
    for candidate in as_dicts(candidates, CANDIDATE_COLUMNS):
        section = by_section.get(candidate.pop("section_id"))
        if section is not None:
            candidate["found_by"] = found_by(candidate, k, min_heading_score)
            section["candidates"].append(candidate)
    for s in result:
        if not s["candidates"]:
            continue
        top_text = max(c["text_score"] for c in s["candidates"])
        top_heading = max(c["heading_score"] for c in s["candidates"])
        for c in s["candidates"]:
            c["best_by_text"] = c["text_score"] >= top_text - TIE
            c["best_by_heading"] = (
                c["heading_score"] >= min_heading_score and c["heading_score"] >= top_heading - TIE
            )
    return result


def attach_template_counts(template_rows, count_rows) -> list[dict]:
    """The guidance's templates in appendix order, each with its sections and, for each
    section, how many review sections have it as their best match by text, by heading, and
    by both."""
    counts = {(t, p): (a, b, c) for t, p, a, b, c in count_rows}
    templates: dict[str, dict] = {}
    for row in template_rows:
        template, appendix, title, kind, source_url, position, heading, printed, text, page = row
        entry = templates.setdefault(
            template,
            {
                "template": template,
                "appendix": appendix,
                "title": title,
                "kind": kind,
                "source_url": source_url,
                "sections": [],
            },
        )
        n_text, n_heading, n_both = counts.get((template, position), (0, 0, 0))
        entry["sections"].append(
            {
                "position": position,
                "heading": heading,
                "printed": printed,
                "guidance": text,
                "page": page,
                "n_best_by_text": n_text,
                "n_best_by_heading": n_heading,
                "n_best_by_both": n_both,
            }
        )
    return list(templates.values())


def run_summary(row) -> dict | None:
    """Convert a matching-run row into the app response, or return None when no run exists."""
    if row is None:
        return None
    revision, model, k, min_heading_score, matched_at, git_commit, n_sections, n_candidates = row
    return {
        "revision": revision,
        "model": model,
        "k": k,
        "min_heading_score": min_heading_score,
        "matched_at": matched_at.isoformat(),
        "git_commit": git_commit,
        "n_sections": n_sections,
        "n_candidates": n_candidates,
    }


def main(
    dataset: Literal["DHRC-100", "DHRC-RAW"] = "DHRC-100",
    guidance: Literal["dhr-2016", "dardr-2026"] = "dhr-2016",
    doc_id: str = "",
    template: str = "",
    template_position: int = 0,
):
    check_dataset(dataset)
    if guidance not in GUIDANCES:
        raise ValueError(f"Unknown guidance {guidance!r}: choose one of {', '.join(GUIDANCES)}")
    with connect() as conn:
        apply_migrations(conn)  # so the tables exist even before any pipeline has run
        run = run_summary(conn.execute(RUN_SQL, (dataset, guidance)).fetchone())
        if run is None:
            return {"dataset": dataset, "guidance": guidance, "run": None}
        k, min_score = run["k"], run["min_heading_score"]
        args = {"revision": run["revision"], "guidance": guidance, "min": min_score}
        if doc_id:
            sections = conn.execute(SECTIONS_SQL, (run["revision"], doc_id)).fetchall()
            candidates = conn.execute(CANDIDATES_SQL, {**args, "doc_id": doc_id}).fetchall()
            return {
                "doc_id": doc_id,
                "sections": attach_candidates(sections, candidates, k, min_score),
            }
        if template:
            args |= {"template": template, "position": template_position, "limit": MAX_MATCHES}
            matches = as_dicts(conn.execute(MATCHES_SQL, args).fetchall(), MATCH_COLUMNS)
            total = conn.execute(MATCHES_COUNT_SQL, args).fetchone()[0]
            return {
                "template": template,
                "template_position": template_position,
                "n_matches": total,
                "matches": matches,
            }
        title = conn.execute(GUIDANCE_SQL, (guidance,)).fetchone()
        templates = conn.execute(TEMPLATES_SQL, (guidance,)).fetchall()
        counts = conn.execute(COUNTS_SQL, args).fetchall()
        reviews = conn.execute(REVIEWS_SQL, args).fetchall()
    return {
        "dataset": dataset,
        "guidance": guidance,
        "title": title[0] if title else guidance,
        "source_url": title[1] if title else None,
        "run": run,
        "templates": attach_template_counts(templates, counts),
        "reviews": as_dicts(reviews, REVIEW_COLUMNS),
    }
