# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#     "psycopg[binary]==3.3.9",
#     "wmill==1.875.0",
# ]
# ///
"""O1 stage 5, app: the template choices and the standardised reviews (Standardised tab).

Read-only.
With only a dataset and guidance: how the choices were made, the guidance's templates (each
with how many reviews are written to it, and each of its sections with how many review
sections were mapped to it), and each review with its template and counts. With doc_id: that
review's template and the templates the model weighed, its sections (each with its choice and
the template sections the model weighed), and the review in standardised order (its
template's sections, each with the review sections mapped to it). With template and
template_position: the review sections mapped to that template section. With status
('doubtful', 'unmatched' or 'no answer'): the sections flagged that way. Doubtful: the model
gave its answer less than 50%, or the section's review has a doubtful template (also under
50%, or a fallback: the model said "none of these" but wasn't sure, so the review was given
its likeliest template). Doubtful answers are the ones to check by hand. Each section also
says how it got its template section (method, refine.py: the model's own choice, two template
sections combined, its parent section's, an extra section, the nearest fit, its subsections',
Appendices or Other content), with mapped_probability, how sure that was, and also_* (the
other template section it combines, or what an appendix is about). A section is doubtful for
its own mapping; review_doubtful says its review's template is. A review's method 'sections'
means it got the template its sections fit best (sections_fit, sections_margin).
"""

import json
from typing import Literal

from ...datasets import check_dataset
from ...db import apply_migrations, connect

GUIDANCES = {"dhr-2016", "dardr-2026"}
MAX_ROWS = 500
DOUBTFUL = 0.5  # the model gave its answer less probability than this
WEIGHED = 0.01  # options the model gave less probability than this aren't listed

VERSION_SQL = """
SELECT c.revision, c.reviews, c.model, c.model_revision, c.prompt_version, c.chosen_at,
       c.git_commit, c.slurm_job_ids, c.blythe_runs, c.n_reviews, c.n_reviews_matched,
       c.n_sections, c.n_matched, c.n_unmatched, c.n_no_answer, c.n_reviews_doubtful,
       c.n_doubtful, c.n_parent, c.n_extra, c.n_nearest, c.refined_at, c.n_combined,
       c.n_subsections, c.n_appendix, c.n_other, c.n_reviews_by_sections, c.run_details,
       g.title, g.source_url
FROM corpus.current_versions AS v
JOIN corpus.choice_versions AS c USING (revision)
JOIN corpus.guidance_documents AS g ON g.guidance = c.guidance
WHERE v.dataset = %s AND c.guidance = %s
"""
VERSION_COLUMNS = (
    "revision",
    "reviews",
    "model",
    "model_revision",
    "prompt_version",
    "chosen_at",
    "git_commit",
    "slurm_job_ids",
    "blythe_runs",
    "n_reviews",
    "n_reviews_matched",
    "n_sections",
    "n_matched",
    "n_unmatched",
    "n_no_answer",
    "n_reviews_doubtful",
    "n_doubtful",
    "n_parent",  # sections given their parent section's template section
    "n_extra",  # sections given an extra section
    "n_nearest",  # sections given the model's likeliest after "none of these"
    "refined_at",
    "n_combined",  # torn between two template sections that together it was sure of
    "n_subsections",  # headings that took their subsections' mapping
    "n_appendix",  # in an appendix
    "n_other",  # the catch-all: nothing else fits
    "n_reviews_by_sections",  # reviews given the template their sections fit best
)
RUN_DETAILS = ("vllm", "gpu", "driver", "prompt_tokens_per_second", "seconds_generating")

TEMPLATES_SQL = """
SELECT t.template, t.appendix, t.title, t.kind, ts.position, ts.heading, ts.printed,
       ts.guidance, ts.page,
       count(c.*) AS n_chosen,
       count(c.*) FILTER (WHERE c.doubtful) AS n_doubtful,
       (SELECT count(*) FROM corpus.document_choices AS d
        WHERE d.revision = %(revision)s AND d.guidance = %(guidance)s
          AND d.template = t.template) AS n_reviews
FROM corpus.templates AS t
JOIN corpus.template_sections AS ts USING (template)
LEFT JOIN corpus.current_template_choices AS c
  ON c.revision = %(revision)s AND c.guidance = %(guidance)s
 AND c.template = ts.template AND c.template_position = ts.position
WHERE t.guidance = %(guidance)s
GROUP BY t.template, t.appendix, t.title, t.kind, ts.position, ts.heading, ts.printed,
         ts.guidance, ts.page
ORDER BY t.appendix, ts.position
"""
REVIEWS_SQL = """
SELECT d.doc_id, d.status, d.template, d.template_title, d.probability, d.error,
       d.fallback, d.doubtful, d.method,
       count(c.*), count(c.*) FILTER (WHERE c.status = 'matched'),
       count(c.*) FILTER (WHERE c.status = 'unmatched'),
       count(c.*) FILTER (WHERE c.status = 'no answer'),
       count(c.*) FILTER (WHERE c.doubtful)
FROM corpus.current_document_choices AS d
LEFT JOIN corpus.current_template_choices AS c USING (revision, guidance, doc_id)
WHERE d.revision = %(revision)s AND d.guidance = %(guidance)s
GROUP BY d.doc_id, d.status, d.template, d.template_title, d.probability, d.error,
         d.fallback, d.doubtful, d.method
ORDER BY d.doubtful DESC, d.doc_id
"""
REVIEW_COLUMNS = (
    "doc_id",
    "status",  # of the review's template: 'matched', 'unmatched' (none of them) or 'no answer'
    "template",
    "template_title",
    "probability",
    "error",
    "fallback",  # the model said "none of these" but wasn't sure: given its likeliest template
    "doubtful",  # its template is doubtful: check by hand (listed first)
    "method",  # 'model', or 'sections': the template its sections fit best
    "n_sections",
    "n_matched",
    "n_unmatched",
    "n_no_answer",
    "n_doubtful",
)

DOCUMENT_SQL = """
SELECT status, template, template_title, template_kind, answer, probability, probabilities,
       error, fallback, doubtful, method, sections_fit, sections_margin, sections_scores,
       sections_filled
FROM corpus.current_document_choices
WHERE revision = %(revision)s AND guidance = %(guidance)s AND doc_id = %(doc_id)s
"""
DOCUMENT_COLUMNS = (
    "status",
    "template",
    "template_title",
    "template_kind",
    "answer",
    "probability",
    "probabilities",
    "error",
    "fallback",
    "doubtful",
    "method",
    "sections_fit",  # share of its text that fits its template (method 'sections')
    "sections_margin",  # how far ahead of the next template that was
    "sections_scores",  # each template's fit, sections filled and score
    "sections_filled",  # how many different sections of its template it fills
)
SECTIONS_SQL = """
SELECT s.section_id, s.parent_id, s.level, s.heading, s.position, s.first_page, s.last_page,
       c.status, c.template, c.template_position, c.template_title, c.template_heading,
       c.answer, c.probability, c.none_probability, c.probabilities, c.n_options, c.error,
       coalesce(c.doubtful, false), c.method, c.mapped_probability, c.extra_answer,
       c.extra_probability, c.extra_probabilities, coalesce(c.review_doubtful, false),
       c.also_template, c.also_position, c.also_heading, c.model_template,
       c.model_template_position
FROM corpus.sections AS s
LEFT JOIN corpus.current_template_choices AS c
  ON c.revision = s.revision AND c.doc_id = s.doc_id AND c.section_id = s.section_id
 AND c.guidance = %(guidance)s
WHERE s.revision = %(revision)s AND s.doc_id = %(doc_id)s
ORDER BY s.position
"""
SECTION_COLUMNS = (
    "section_id",
    "parent_id",
    "level",
    "heading",
    "position",
    "first_page",
    "last_page",
    "status",  # None: this section wasn't asked about (e.g. a pilot run)
    "template",
    "template_position",
    "template_title",
    "template_heading",
    "answer",
    "probability",
    "none_probability",
    "probabilities",
    "n_options",
    "error",
    "doubtful",
    "method",  # how it got its mapping (refine.METHODS)
    "mapped_probability",  # how sure the mapping is, whichever way it was made
    "extra_answer",  # the extra-sections question, if it was asked
    "extra_probability",
    "extra_probabilities",
    "review_doubtful",  # its review's template is doubtful
    "also_template",  # combined: the other template section; appendix: what it is about
    "also_position",
    "also_heading",
    "model_template",  # the model's own choice
    "model_template_position",
)
# Stage 4's ranks for each section's candidates, to compare with what the model chose.
RANKS_SQL = """
SELECT c.section_id, c.template, c.template_position, c.heading_rank, c.text_rank
FROM corpus.template_candidates AS c
JOIN corpus.templates AS t USING (template)
WHERE c.revision = %(revision)s AND c.doc_id = %(doc_id)s AND t.guidance = %(guidance)s
"""
STANDARDISED_SQL = """
SELECT part, status, template, template_title, template_position, template_heading,
       n_sections, section_ids, min_probability, n_opening_blocks
FROM corpus.current_standardised
WHERE revision = %(revision)s AND guidance = %(guidance)s AND doc_id = %(doc_id)s
ORDER BY part
"""
STANDARDISED_COLUMNS = (
    "part",
    "status",  # 'template section', 'unmatched' or 'no answer'
    "template",
    "template_title",
    "template_position",
    "template_heading",
    "n_sections",
    "section_ids",  # the review's sections in this part, in the review's order
    "min_probability",
    "n_opening_blocks",  # text before the review's first heading (its title page) shown here
)

# Review sections mapped to one template section, or flagged with one status.
FOUND_SQL = """
SELECT c.doc_id, c.section_id, c.section_level, c.section_heading, c.section_position,
       c.mapped_probability, c.none_probability, c.error, c.doubtful, c.template_heading,
       c.method, c.also_heading
FROM corpus.current_template_choices AS c
WHERE c.revision = %(revision)s AND c.guidance = %(guidance)s AND {where}
ORDER BY c.mapped_probability {order} NULLS LAST, c.doc_id, c.section_position
LIMIT %(limit)s
"""
COUNT_SQL = """
SELECT count(*) FROM corpus.current_template_choices AS c
WHERE c.revision = %(revision)s AND c.guidance = %(guidance)s AND {where}
"""
WHERE = {
    "template": "c.template = %(template)s AND c.template_position = %(position)s",
    "doubtful": "c.doubtful",
    "unmatched": "c.status = 'unmatched'",
    "no answer": "c.status = 'no answer'",
}
FOUND_COLUMNS = (
    "doc_id",
    "section_id",
    "level",
    "heading",
    "position",
    "probability",
    "none_probability",
    "error",
    "doubtful",
    "template_heading",  # what it was mapped to
    "method",
    "also_heading",
)


def as_dicts(rows, columns) -> list[dict]:
    return [dict(zip(columns, row, strict=True)) for row in rows]


def as_json(value) -> dict:
    return value if isinstance(value, dict) else json.loads(value or "{}")


def version_summary(row) -> dict | None:
    if row is None:
        return None
    version = dict(zip(VERSION_COLUMNS, row[: len(VERSION_COLUMNS)], strict=True))
    version["chosen_at"] = version["chosen_at"].isoformat()
    if version["refined_at"]:
        version["refined_at"] = version["refined_at"].isoformat()
    details, title, source_url = row[len(VERSION_COLUMNS) :]
    details = as_json(details)
    # The section job's details, or the document job's if there was no section job.
    run = details.get("sections") or details.get("documents") or {}
    version["run"] = {key: run.get(key) for key in RUN_DETAILS}
    version["guidance_title"], version["guidance_url"] = title, source_url
    return version


def group_templates(rows) -> list[dict]:
    """The guidance's templates in appendix order, each section with its counts."""
    templates: dict[str, dict] = {}
    for row in rows:
        template, appendix, title, kind, position, heading, printed, text, page = row[:9]
        n_chosen, n_doubtful, n_reviews = row[9:]
        entry = templates.setdefault(
            template,
            {
                "template": template,
                "appendix": appendix,
                "title": title,
                "kind": kind,
                "n_reviews": n_reviews,
                "sections": [],
            },
        )
        entry["sections"].append(
            {
                "position": position,
                "heading": heading,
                "printed": printed,
                "guidance": text,
                "page": page,
                "n_chosen": n_chosen,
                "n_doubtful": n_doubtful,
            }
        )
    return list(templates.values())


def weighed_templates(document: dict, templates: dict[str, dict]) -> list[dict]:
    """The templates the model gave any real probability for a review, likeliest first."""
    probabilities = as_json(document["probabilities"])
    options = []
    for key, p in probabilities.items():
        if p < WEIGHED and key != document["template"]:
            continue
        t = templates.get(key)
        options.append(
            {
                "template": None if key == "none" else key,
                "title": "None of these" if key == "none" else (t["title"] if t else key),
                "kind": t["kind"] if t else None,
                "probability": p,
                "chosen": key == document["template"]
                or (key == "none" and document["status"] == "unmatched"),
            }
        )
    return sorted(options, key=lambda o: -o["probability"])


def attach_weighed(sections, ranks, templates: dict[str, dict]) -> list[dict]:
    """Each section with the template sections the model gave any real probability,
    likeliest first, each with stage 4's ranks for it if it was one of stage 4's candidates."""
    result = as_dicts(sections, SECTION_COLUMNS)
    rank = {(s, t, p): (h, x) for s, t, p, h, x in ranks}
    headings = {
        f"{t['template']}#{s['position']}": (t, s)
        for t in templates.values()
        for s in t["sections"]
    }
    for section in result:
        probabilities = as_json(section["probabilities"])
        section["probabilities"] = probabilities
        section["extra_probabilities"] = as_json(section["extra_probabilities"])
        own = section["model_template"]  # what the model chose, whatever the rules made of it
        chosen = f"{own}#{section['model_template_position']}" if own else None
        weighed = []
        for key, p in probabilities.items():
            if p < WEIGHED and key != chosen:
                continue
            if key == "none":
                weighed.append(
                    {
                        "template": None,
                        "template_position": None,
                        "template_title": None,
                        "template_heading": "None of these",
                        "probability": p,
                        "chosen": section["answer"] == "Z",
                        "heading_rank": None,
                        "text_rank": None,
                    }
                )
                continue
            t, s = headings.get(key, (None, None))
            template, position = key.rsplit("#", 1)
            h_rank, t_rank = rank.get(
                (section["section_id"], template, int(position)), (None, None)
            )
            weighed.append(
                {
                    "template": template,
                    "template_position": int(position),
                    "template_title": t["title"] if t else template,
                    "template_heading": s["heading"] if s else key,
                    "probability": p,
                    "chosen": key == chosen,
                    "heading_rank": h_rank,
                    "text_rank": t_rank,
                }
            )
        section["weighed"] = sorted(weighed, key=lambda o: -o["probability"])
    return result


def main(
    dataset: Literal["DHRC-100", "DHRC-RAW"] = "DHRC-100",
    guidance: Literal["dhr-2016", "dardr-2026"] = "dhr-2016",
    doc_id: str = "",
    template: str = "",
    template_position: int = 0,
    status: Literal["", "doubtful", "unmatched", "no answer"] = "",
):
    check_dataset(dataset)
    if guidance not in GUIDANCES:
        raise ValueError(f"Unknown guidance {guidance!r}: choose one of {sorted(GUIDANCES)}")
    with connect() as conn:
        apply_migrations(conn)  # so the tables exist even before any choices are made
        version = version_summary(conn.execute(VERSION_SQL, (dataset, guidance)).fetchone())
        if version is None:
            return {"dataset": dataset, "guidance": guidance, "version": None}
        args = {
            "revision": version["revision"],
            "guidance": guidance,
            "doubtful": DOUBTFUL,
            "limit": MAX_ROWS,
        }
        if template or status:
            key = "template" if template else status
            if key not in WHERE:
                raise ValueError(f"Unknown status {status!r}")
            args |= {"template": template, "position": template_position}
            order = "DESC" if template else "ASC"  # surest first; flagged: least sure first
            rows = conn.execute(FOUND_SQL.format(where=WHERE[key], order=order), args).fetchall()
            total = conn.execute(COUNT_SQL.format(where=WHERE[key]), args).fetchone()[0]
            return {"n_found": total, "found": as_dicts(rows, FOUND_COLUMNS)}
        templates = group_templates(conn.execute(TEMPLATES_SQL, args).fetchall())
        if doc_id:
            args["doc_id"] = doc_id
            by_template = {t["template"]: t for t in templates}
            row = conn.execute(DOCUMENT_SQL, args).fetchone()
            document = dict(zip(DOCUMENT_COLUMNS, row, strict=True)) if row else None
            if document:
                document["weighed"] = weighed_templates(document, by_template)
                document["probabilities"] = as_json(document["probabilities"])
                document["sections_scores"] = as_json(document["sections_scores"])
            sections = conn.execute(SECTIONS_SQL, args).fetchall()
            ranks = conn.execute(RANKS_SQL, args).fetchall()
            parts = as_dicts(conn.execute(STANDARDISED_SQL, args).fetchall(), STANDARDISED_COLUMNS)
            return {
                "doc_id": doc_id,
                "document": document,  # None: this review wasn't asked about (a pilot run)
                "sections": attach_weighed(sections, ranks, by_template),
                "standardised": parts,
            }
        reviews = as_dicts(conn.execute(REVIEWS_SQL, args).fetchall(), REVIEW_COLUMNS)
    return {
        "dataset": dataset,
        "guidance": guidance,
        "version": version,
        "doubtful_below": DOUBTFUL,
        "n_doubtful": version["n_doubtful"],
        "templates": templates,
        "reviews": reviews,
    }
