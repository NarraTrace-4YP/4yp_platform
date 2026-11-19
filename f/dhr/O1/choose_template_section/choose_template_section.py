# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#     "mlflow-skinny==3.18.0",
#     "paramiko==5.1.0",
#     "psycopg[binary]==3.3.9",
#     "pyotp==2.12.1",
#     "wmill==1.875.0",
# ]
# ///
"""Map each review onto a statutory template with an LLM on Blythe (O1 stage 5).

For the newest grouped version of a dataset, against one guidance, in two GPU jobs:
  1. documents: one prompt per review asks which of the guidance's templates the whole review
     is written to (overview report, executive summary, ...) or none (prompts.py).
  2. sections: one prompt per section of every review that got a template asks which
     section of that template, or of the action plan that goes with it, the section is, or
     none. A review is mapped onto one template, not piece by piece across several (Rowan's
     choice, 19 Nov 2026). Every section of the template is an option, not only stage 4's
     candidates, so a right answer is never left out; stage 4's ranks are kept to compare.
Each job is the fixed llm_batch job on Blythe (llm_batch.py in narratrace-slurm), answering
with vLLM on one GPU. The answers go into corpus.document_choices and corpus.template_choices;
sections that match none are kept and flagged as unmatched. If the model says a review is
written to none of the templates but is less than FALLBACK_BELOW sure, the review is given its
likeliest template anyway (Rowan, 19 Nov 2026: a review is often only part of a report) and
marked doubtful, like every answer under 50%. Choosing again for the same
dataset version and guidance replaces its choices. reviews="pilot" does only the
specification's 10 pilot reviews, to check the answers and the speed first.

This is a workflow: after submitting each job it pauses without holding a Windmill worker
until the job's last step wakes it (or a safety check every check_every_minutes), checks
with SLURM that the job has ended, and downloads the answers. Each step that talks to Blythe
logs in with Rowan's own Blythe account (f/dhr/blythe.py), so only Rowan can run this:
anyone else's run stops at its first line. Cancelling this run does not cancel a SLURM job on
Blythe: use `scancel <job>` there.

dry_run=True builds the document prompts and shows a sample, without contacting Blythe or
changing the database (the section prompts depend on the documents' answers).
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import math
import secrets
import time
import zlib
from datetime import datetime, timezone
from typing import Literal

from wmill import get_approval_urls, step, task, wait_for_approval, workflow

from ...blythe import MAX_HOURS, Blythe, BlytheError, check_owner
from ...datasets import check_dataset
from ...db import apply_migrations, connect
from ...settings import use_mlflow
from ...version import GIT_COMMIT
from .prompts import (
    NONE,
    PROMPT_VERSION,
    Document,
    Section,
    Template,
    TemplateSection,
    action_plans,
    document_prompt,
    interpret,
    interpret_document,
    section_prompt,
)

MODELS = {"Qwen/Qwen3.8-27B-FP8"}  # must be downloaded on Blythe by setup_env.sbatch
GUIDANCES = {"dhr-2016", "dardr-2026"}
# The specification's pilot set (section 5.2): the same 10 reviews through every stage.
PILOT_REVIEWS = [
    "Mary - Bromley - 2018_a - DHR",
    "Krystyna, Elzbieta - Wiltshire - 2020_a - DHR",
    "Maria - Nottingham - 2020_a - DHR",
    "Senai - Hammersmith and Fulham - 2020_a - DHR",
    "Betty - Ceredigion - 2019_a - DHR",
    "Penny - Somerset - 2019_a - DHR",
    "Steven - Stoke-on-Trent - 2018_a - DHR",
    "Rachel - Brent - 2018_a - DHR",
    "Jeera, Amrinder - Sandwell - 2020_a - DHR",
    "Christopher - North Hertfordshire - 2018_a - DHR",
]
# vLLM settings sent with the prompts (narratrace-slurm's llm_batch.py explains each).
LLM_SETTINGS = {
    "max_model_len": 8192,
    "top_logprobs": 20,
    "chunk_size": 512,
    "gpu_memory_utilization": 0.9,
    "seed": 0,
}
DOUBTFUL = 0.5  # answers the model gave less probability than this are counted as doubtful
# A review the model says matches none of the templates, with less probability than this, is
# given its likeliest template anyway, and it and its sections are marked doubtful.
FALLBACK_BELOW = 0.8
NOT_ASKED = "not asked: its review matched none of the templates"
NO_DOCUMENT = "not asked: there was no answer for its review's template"


def log(message: str) -> None:
    print(message, flush=True)


# ---------------------------------------------------------------- reading the database

REVISION_SQL = """
SELECT v.revision FROM corpus.current_versions AS v
JOIN corpus.candidate_versions AS c ON c.revision = v.revision AND c.guidance = %(guidance)s
WHERE v.dataset = %(dataset)s
"""
TEMPLATES_SQL = """
SELECT t.template, t.appendix, t.title, t.kind, ts.position, ts.heading, ts.guidance
FROM corpus.templates AS t
JOIN corpus.template_sections AS ts USING (template)
WHERE t.guidance = %(guidance)s
ORDER BY t.appendix, ts.position
"""
# Each section with the start of its own text and its subsections' headings, as stage 4
# reads them; parent_id gives the headings of the sections it sits in.
SECTIONS_SQL = """
WITH own AS (
    SELECT doc_id, section_id,
           left(string_agg(text, chr(10) ORDER BY position), %(chars)s) AS text
    FROM corpus.blocks
    WHERE revision = %(revision)s AND section_id IS NOT NULL
      AND block_type <> 'SectionHeader' AND text <> ''
      AND (%(docs)s::text[] IS NULL OR doc_id = ANY(%(docs)s))
    GROUP BY doc_id, section_id
), children AS (
    SELECT doc_id, parent_id AS section_id,
           string_agg(heading, '; ' ORDER BY position) AS headings
    FROM corpus.sections
    WHERE revision = %(revision)s AND parent_id IS NOT NULL
      AND (%(docs)s::text[] IS NULL OR doc_id = ANY(%(docs)s))
    GROUP BY doc_id, parent_id
)
SELECT s.doc_id, s.section_id, s.parent_id, s.level, s.heading, coalesce(own.text, ''),
       coalesce(c.headings, '')
FROM corpus.sections AS s
LEFT JOIN own USING (doc_id, section_id)
LEFT JOIN children AS c USING (doc_id, section_id)
WHERE s.revision = %(revision)s AND (%(docs)s::text[] IS NULL OR s.doc_id = ANY(%(docs)s))
ORDER BY s.doc_id, s.position
"""
# Each review's text before its first heading (title page, contents...).
OPENINGS_SQL = """
SELECT doc_id, left(string_agg(text, ' ' ORDER BY position), %(chars)s)
FROM corpus.blocks
WHERE revision = %(revision)s AND section_id IS NULL AND text <> ''
  AND (%(docs)s::text[] IS NULL OR doc_id = ANY(%(docs)s))
GROUP BY doc_id
"""


def check_choices(dataset: str, guidance: str, reviews: str, model: str) -> None:
    check_dataset(dataset)
    if guidance not in GUIDANCES:
        raise ValueError(f"Unknown guidance {guidance!r}: choose one of {sorted(GUIDANCES)}")
    if reviews not in ("pilot", "all"):
        raise ValueError(f"reviews must be 'pilot' or 'all', not {reviews!r}")
    if model not in MODELS:
        raise ValueError(f"Unknown model {model!r}: choose one of {sorted(MODELS)}")


def read_inputs(conn, dataset: str, guidance: str, reviews: str) -> dict:
    """The dataset version, its reviews and sections, and the guidance's templates."""
    row = conn.execute(REVISION_SQL, {"dataset": dataset, "guidance": guidance}).fetchone()
    if not row:
        raise ValueError(
            f"{dataset}'s newest version has no {guidance} candidates yet: run"
            " f/dhr/O1/find_candidate_template_sections/find_candidate_template_sections first"
            " (it also stores the guidance's templates)"
        )
    query = {
        "revision": row[0],
        "guidance": guidance,
        "chars": 4000,  # more than prompts.py shows, which then cuts it at a word
        "docs": PILOT_REVIEWS if reviews == "pilot" else None,
    }
    templates: dict[str, Template] = {}
    for template, appendix, title, kind, position, heading, text in conn.execute(
        TEMPLATES_SQL, query
    ):
        templates.setdefault(template, Template(template, appendix, title, kind)).sections.append(
            TemplateSection(template, position, heading, text)
        )
    rows = conn.execute(SECTIONS_SQL, query).fetchall()
    headings = {(doc, section): heading for doc, section, _, _, heading, _, _ in rows}
    parents = {(doc, section): parent for doc, section, parent, _, _, _, _ in rows}
    openings = dict(conn.execute(OPENINGS_SQL, query).fetchall())
    sections, documents = [], {}
    for doc, section, parent, level, heading, text, subsections in rows:
        path, up = [], parent
        while up is not None:
            path.insert(0, headings[(doc, up)])
            up = parents[(doc, up)]
        sections.append(Section(doc, section, heading, path, text, subsections))
        documents.setdefault(doc, Document(doc, openings.get(doc, ""), [])).headings.append(
            (level, heading)
        )
    if reviews == "pilot":
        missing = [doc for doc in PILOT_REVIEWS if doc not in documents]
        if missing:
            raise ValueError(f"Pilot reviews missing from {dataset}: {missing}")
    guidance_title = conn.execute(
        "SELECT title FROM corpus.guidance_documents WHERE guidance = %s", (guidance,)
    ).fetchone()[0]
    return {
        "revision": row[0],
        "templates": templates,
        "documents": list(documents.values()),
        "sections": sections,
        "guidance_title": guidance_title,
    }


# ---------------------------------------------------------------- prompts and answers


def document_prompts(inputs: dict) -> list[dict]:
    templates = list(inputs["templates"].values())
    return [document_prompt(d, templates, inputs["guidance_title"]) for d in inputs["documents"]]


def section_prompts(inputs: dict, document_templates: dict[str, str | None]) -> list[dict]:
    """A prompt for every section of each review that was given a template, grouped by
    template so prompts that start alike follow each other."""
    plans = action_plans(list(inputs["templates"].values()))
    prompts = []
    for section in inputs["sections"]:
        template = document_templates.get(section.doc_id)
        if template:
            prompts.append(
                (
                    template,
                    section_prompt(
                        section, inputs["templates"][template], plans, inputs["guidance_title"]
                    ),
                )
            )
    return [p for _, p in sorted(prompts, key=lambda pair: pair[0])]


def gzip_lines(rows: list[dict]) -> bytes:
    """JSON lines, gzipped with a fixed timestamp so the same prompts give the same bytes."""
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as f:
        for row in rows:
            f.write((json.dumps(row, ensure_ascii=False) + "\n").encode())
    return buffer.getvalue()


def gunzip_complete(raw: bytes) -> bytes:
    """Unzips a file of gzip members, keeping whatever a cut-off last member still holds."""
    out, data = bytearray(), raw
    while data:
        member = zlib.decompressobj(16 + zlib.MAX_WBITS)
        try:
            out += member.decompress(data)
        except zlib.error:
            break
        if not member.eof:
            break
        data = member.unused_data
    return bytes(out)


def read_results(raw: bytes) -> list[dict]:
    """llm_batch's results, keeping every complete line (the last can be cut off if the job
    was killed while writing)."""
    results = []
    for line in gunzip_complete(raw).decode("utf-8", errors="replace").splitlines():
        try:
            results.append(json.loads(line))
        except ValueError:
            break
    return results


def automatic_hours(prompt_chars: int) -> int:
    """A generous SLURM time limit before a full run has measured the speed: about 1,000
    prompt tokens a second (4 characters a token), times 1.5, plus an hour to start."""
    hours = prompt_chars / 4 / 1000 / 3600 * 1.5 + 1
    return max(1, min(MAX_HOURS, math.ceil(hours)))


def prompt_chars(prompts: list[dict]) -> int:
    return sum(len(m["content"]) for p in prompts for m in p["messages"])


def document_rows(results: list[dict], documents: list[Document]) -> list[dict]:
    """One row per review: its interpreted answer, or 'no result' if the job never got to it."""
    by_doc = {}
    for result in results:
        row = interpret_document(result)
        by_doc[row["doc_id"]] = row
    rows = []
    for document in documents:
        rows.append(
            by_doc.pop(document.doc_id, None)
            or {
                "doc_id": document.doc_id,
                "template": None,
                "answer": None,
                "probability": None,
                "probabilities": {},
                "error": "no result: the job ended before answering it",
            }
        )
    if by_doc:
        raise ValueError(f"{len(by_doc)} answers are for reviews that weren't asked about")
    return rows


def apply_fallback(rows: list[dict]) -> list[dict]:
    """A review answered "none of these" with less than FALLBACK_BELOW probability gets its
    likeliest template instead (marked fallback, so doubtful). "none" itself is kept as the
    answer and its probability, so the record shows what the model said."""
    for row in rows:
        row.setdefault("fallback", False)
        if row["answer"] != NONE or (row["probability"] or 0) >= FALLBACK_BELOW:
            continue
        templates = {k: p for k, p in row["probabilities"].items() if k != "none"}
        if templates:
            row["template"] = max(templates, key=templates.get)
            row["fallback"] = True
    return rows


def choice_rows(
    results: list[dict], sections: list[Section], documents: dict[str, dict]
) -> list[dict]:
    """One row per section: its interpreted answer; for a section of a review with no
    template, why it wasn't asked; or 'no result' if the job never got to it."""
    by_section = {}
    for result in results:
        row = interpret(result)
        by_section[(row["doc_id"], row["section_id"])] = row
    rows = []
    for section in sections:
        key = (section.doc_id, section.section_id)
        document = documents[section.doc_id]
        if document["template"] is None:
            error = NOT_ASKED if document["answer"] == NONE else NO_DOCUMENT
        else:
            error = "no result: the job ended before answering it"
        rows.append(
            by_section.pop(key, None)
            or {
                "doc_id": section.doc_id,
                "section_id": section.section_id,
                "template": None,
                "template_position": None,
                "answer": None,
                "probability": None,
                "none_probability": None,
                "probabilities": {},
                "n_options": 0,
                "error": error,
            }
        )
    if by_section:
        raise ValueError(f"{len(by_section)} answers are for sections that weren't asked about")
    return rows


# ---------------------------------------------------------------- tasks (each its own Windmill job)


@task
async def preview(dataset: str, guidance: str, reviews: str):
    """dry_run: the document prompts that would be sent, without Blythe or any database change."""
    started = time.time()
    with connect() as conn:
        inputs = read_inputs(conn, dataset, guidance, reviews)
    prompts = document_prompts(inputs)
    templates = list(inputs["templates"].values())
    plans = action_plans(templates)
    reports = [t for t in templates if t.kind == "overview report"] or [
        t for t in templates if t not in plans
    ]
    section = inputs["sections"][min(1, len(inputs["sections"]) - 1)]
    sample_section = section_prompt(section, reports[0], plans, inputs["guidance_title"])
    n_options = {t.template: len(t.sections) for t in inputs["templates"].values()}
    return {
        "result": "Dry run: nothing was sent to Blythe or stored.",
        "dataset": dataset,
        "revision": inputs["revision"],
        "guidance": guidance,
        "reviews": reviews,
        "prompt_version": PROMPT_VERSION,
        "n_reviews": len(inputs["documents"]),
        "n_sections": len(inputs["sections"]),
        "template_sections": n_options,
        "document_prompt_chars_max": max(len(p["messages"][1]["content"]) for p in prompts),
        "document_prompts_file_mb": round(len(gzip_lines(prompts)) / 1e6, 2),
        "section_prompt_chars_example": len(sample_section["messages"][1]["content"]),
        "seconds": round(time.time() - started, 1),
        "sample_document_prompt": prompts[0]["messages"][1]["content"],
        f"sample_section_prompt_if_{reports[0].kind.replace(' ', '_')}": sample_section["messages"][
            1
        ]["content"],
    }


async def _submit(
    phase: str, request: dict, prompts: list[dict], run_id: str, callback_url: str, hours: int
) -> dict:
    data = gzip_lines(prompts)
    log(
        f"Sending {len(prompts)} {phase} prompts ({len(data) / 1e6:.1f} MB) to Blythe,"
        f" with a {hours} h limit"
    )
    with Blythe() as b:
        submitted = b.submit(
            "llm_batch",
            run_id,
            {
                "request.json": json.dumps({**request, "phase": phase}, indent=2),
                "prompts.jsonl.gz": data,
            },
            callback_url,
            hours=hours,
        )
    return {
        **submitted,
        "run_id": run_id,
        "n_prompts": len(prompts),
        "prompts_sha256": hashlib.sha256(data).hexdigest(),
        "hours": hours,
    }


def _request(dataset: str, guidance: str, reviews: str, model: str, revision: str) -> dict:
    return {
        "model": model,
        **LLM_SETTINGS,
        "purpose": "O1 stage 5: choose_template_section",
        "dataset": dataset,
        "revision": revision,
        "guidance": guidance,
        "reviews": reviews,
        "prompt_version": PROMPT_VERSION,
        "git_commit": GIT_COMMIT,
    }


@task
async def submit_documents(
    dataset: str, guidance: str, reviews: str, model: str, run_id: str, callback_url: str
):
    with connect() as conn:
        applied = apply_migrations(conn)
        if applied:
            log(f"OK    Database tables updated: {', '.join(applied)}")
        inputs = read_inputs(conn, dataset, guidance, reviews)
    prompts = document_prompts(inputs)
    submitted = await _submit(
        "documents",
        _request(dataset, guidance, reviews, model, inputs["revision"]),
        prompts,
        run_id,
        callback_url,
        automatic_hours(prompt_chars(prompts)),
    )
    return {**submitted, "revision": inputs["revision"], "model": model}


@task
async def check(run_dir: str, slurm_job_id: str, next_callback_url: str):
    with Blythe() as b:
        state = b.job_state(slurm_job_id)
        log(f"SLURM job {slurm_job_id}: {state}")
        if not state["finished"]:
            b.set_callback(run_dir, next_callback_url)
    return state


@task
async def cancel(slurm_job_id: str):
    with Blythe() as b:
        return b.cancel(slurm_job_id)


def download(submitted: dict, state: dict) -> tuple[list[dict], dict, str]:
    """A finished job's results, summary and the end of its log."""
    run_dir = submitted["run_dir"]
    with Blythe() as b:
        log_tail = b.tail(f"{run_dir}/slurm-{submitted['slurm_job_id']}.out", 60)
        if not b.exists(f"{run_dir}/output/results.jsonl.gz"):
            raise BlytheError(
                f"The job ended ({state.get('state')}) without writing any results."
                f" End of its log:\n{log_tail}"
            )
        raw = b.read(f"{run_dir}/output/results.jsonl.gz")
        summary = (
            json.loads(b.read(f"{run_dir}/output/summary.json"))
            if b.exists(f"{run_dir}/output/summary.json")
            else {}
        )
        b.set_callback(run_dir, None)
    if summary.get("prompts_sha256") not in (None, submitted["prompts_sha256"]):
        raise ValueError("The prompts on Blythe aren't the ones this run sent. Nothing stored.")
    results = read_results(raw)
    log(f"Read {len(results)} answers for {submitted['n_prompts']} prompts")
    return results, summary, log_tail


@task
async def submit_sections(
    dataset: str,
    guidance: str,
    reviews: str,
    documents_job: dict,
    documents_state: dict,
    hours: int,
    run_id: str,
    callback_url: str,
):
    """Reads the document answers, then sends a prompt for every section of each review
    that was given a template."""
    results, summary, _ = download(documents_job, documents_state)
    with connect() as conn:
        inputs = read_inputs(conn, dataset, guidance, reviews)
    if inputs["revision"] != documents_job["revision"]:
        raise ValueError(
            f"{dataset} has been reloaded or regrouped since the document prompts"
            " were made. Nothing stored."
        )
    documents = apply_fallback(document_rows(results, inputs["documents"]))
    fallbacks = [d["doc_id"] for d in documents if d["fallback"]]
    if fallbacks:
        log(
            f"DOUBT {len(fallbacks)} reviews given their likeliest template as a fallback: {fallbacks}"
        )
    by_kind: dict[str, int] = {}
    for row in documents:
        kind = (
            inputs["templates"][row["template"]].kind
            if row["template"]
            else ("none of them" if row["answer"] == NONE else "no answer")
        )
        by_kind[kind] = by_kind.get(kind, 0) + 1
    log(f"OK    The {len(documents)} reviews are written to: {by_kind}")
    prompts = section_prompts(inputs, {row["doc_id"]: row["template"] for row in documents})
    base = {"documents": documents, "documents_summary": summary, "reviews_by_kind": by_kind}
    if not prompts:
        return {**base, "sections_job": None}
    submitted = await _submit(
        "sections",
        _request(dataset, guidance, reviews, documents_job["model"], inputs["revision"]),
        prompts,
        run_id,
        callback_url,
        hours or automatic_hours(prompt_chars(prompts)),
    )
    return {**base, "sections_job": submitted}


STORE_VERSION_SQL = """
INSERT INTO corpus.choice_versions (revision, guidance, reviews, model, model_revision,
    prompt_version, prompts_sha256, run_details, slurm_job_ids, blythe_runs, git_commit,
    n_reviews, n_reviews_matched, n_sections, n_matched, n_unmatched, n_no_answer,
    n_reviews_doubtful, n_doubtful)
VALUES (%(revision)s, %(guidance)s, %(reviews)s, %(model)s, %(model_revision)s,
    %(prompt_version)s, %(prompts_sha256)s, %(run_details)s, %(slurm_job_ids)s, %(blythe_runs)s,
    %(git_commit)s, %(n_reviews)s, %(n_reviews_matched)s, %(n_sections)s, %(n_matched)s,
    %(n_unmatched)s, %(n_no_answer)s, %(n_reviews_doubtful)s, %(n_doubtful)s)
"""
# How often the LLM's choice is also the candidate stage 4 ranked first.
AGREEMENT_SQL = """
SELECT count(*) FILTER (WHERE c.text_rank = 1),
       count(*) FILTER (WHERE c.heading_rank = 1 AND c.heading_score >= v.min_heading_score)
FROM corpus.template_choices AS tc
JOIN corpus.template_candidates AS c
  ON c.revision = tc.revision AND c.doc_id = tc.doc_id AND c.section_id = tc.section_id
 AND c.template = tc.template AND c.template_position = tc.template_position
JOIN corpus.candidate_versions AS v ON v.revision = tc.revision AND v.guidance = tc.guidance
WHERE tc.revision = %(revision)s AND tc.guidance = %(guidance)s
"""


def store(conn, version: dict, documents: list[dict], rows: list[dict]) -> None:
    """Replaces the dataset version's choices for the guidance, all in one transaction."""
    revision, guidance = version["revision"], version["guidance"]
    with conn.transaction():
        conn.execute(
            "DELETE FROM corpus.choice_versions WHERE revision = %s AND guidance = %s",
            (revision, guidance),
        )
        conn.execute(STORE_VERSION_SQL, version)
        with conn.cursor() as cursor:
            with cursor.copy(
                "COPY corpus.document_choices (revision, doc_id, guidance, template, answer,"
                " probability, probabilities, error, fallback) FROM STDIN"
            ) as copy:
                for d in documents:
                    copy.write_row(
                        (
                            revision,
                            d["doc_id"],
                            guidance,
                            d["template"],
                            d["answer"],
                            d["probability"],
                            json.dumps(d["probabilities"]),
                            d["error"],
                            d.get("fallback", False),
                        )
                    )
            with cursor.copy(
                "COPY corpus.template_choices (revision, doc_id, section_id, guidance, template,"
                " template_position, answer, probability, none_probability, probabilities,"
                " n_options, error) FROM STDIN"
            ) as copy:
                for r in rows:
                    copy.write_row(
                        (
                            revision,
                            r["doc_id"],
                            r["section_id"],
                            guidance,
                            r["template"],
                            r["template_position"],
                            r["answer"],
                            r["probability"],
                            r["none_probability"],
                            json.dumps(r["probabilities"]),
                            r["n_options"],
                            r["error"],
                        )
                    )


def doubtful_review(document: dict) -> bool:
    probability = document.get("probability")
    return document["template"] is not None and (
        document.get("fallback", False) or (probability is not None and probability < DOUBTFUL)
    )


def counts(documents: list[dict], rows: list[dict]) -> dict:
    unmatched_docs = {
        d["doc_id"] for d in documents if d["answer"] == NONE and d["template"] is None
    }
    doubtful_docs = {d["doc_id"] for d in documents if doubtful_review(d)}
    return {
        "n_reviews": len(documents),
        "n_reviews_matched": sum(1 for d in documents if d["template"] is not None),
        "n_sections": len(rows),
        "n_matched": sum(1 for r in rows if r["template"] is not None),
        "n_unmatched": sum(
            1
            for r in rows
            if r["answer"] == NONE or (r["answer"] is None and r["doc_id"] in unmatched_docs)
        ),
        "n_no_answer": sum(
            1 for r in rows if r["answer"] is None and r["doc_id"] not in unmatched_docs
        ),
        "n_reviews_doubtful": len(doubtful_docs),
        "n_doubtful": sum(
            1
            for r in rows
            if r["template"] is not None
            and ((r["probability"] or 0) < DOUBTFUL or r["doc_id"] in doubtful_docs)
        ),
    }


@task
async def load_results(
    dataset: str,
    guidance: str,
    reviews: str,
    documents_job: dict,
    sections: dict,
    sections_state: dict | None,
):
    if sections["sections_job"]:
        results, summary, log_tail = download(sections["sections_job"], sections_state)
    else:
        results, summary, log_tail = [], {}, "(no section job: no review was given a template)"
    documents = sections["documents"]
    with connect() as conn:
        apply_migrations(conn)
        inputs = read_inputs(conn, dataset, guidance, reviews)
        if inputs["revision"] != documents_job["revision"]:
            raise ValueError(
                f"{dataset} has been reloaded or regrouped since the prompts were"
                " made. Nothing stored."
            )
        rows = choice_rows(results, inputs["sections"], {d["doc_id"]: d for d in documents})
        jobs = [documents_job] + ([sections["sections_job"]] if sections["sections_job"] else [])
        tally = counts(documents, rows)
        version = {
            "revision": documents_job["revision"],
            "guidance": guidance,
            "reviews": reviews,
            "model": documents_job["model"],
            "model_revision": summary.get("model_revision")
            or sections["documents_summary"].get("model_revision"),
            "prompt_version": PROMPT_VERSION,
            "prompts_sha256": json.dumps({j["run_id"]: j["prompts_sha256"] for j in jobs}),
            "run_details": json.dumps(
                {
                    "documents": sections["documents_summary"],
                    "sections": summary,
                    "sections_state": sections_state,
                    "hours": [j["hours"] for j in jobs],
                    "doubtful_below": DOUBTFUL,
                    "fallback_below": FALLBACK_BELOW,
                }
            ),
            "slurm_job_ids": [j["slurm_job_id"] for j in jobs],
            "blythe_runs": [j["run_id"] for j in jobs],
            "git_commit": GIT_COMMIT,
            **tally,
        }
        store(conn, version, documents, rows)
        same_text, same_heading = conn.execute(
            AGREEMENT_SQL, {"revision": version["revision"], "guidance": guidance}
        ).fetchone()
        by_template = dict(
            conn.execute(
                "SELECT coalesce(t.title, 'none'), count(*) FROM corpus.template_choices AS c"
                " LEFT JOIN corpus.templates AS t USING (template)"
                " WHERE c.revision = %s AND c.guidance = %s GROUP BY 1 ORDER BY 2 DESC",
                (version["revision"], guidance),
            ).fetchall()
        )

    matched = [r for r in rows if r["template"] is not None]
    run = summary or sections["documents_summary"]
    result = {
        "dataset": dataset,
        "revision": version["revision"],
        "guidance": guidance,
        "reviews": reviews,
        "model": version["model"],
        "model_revision": version["model_revision"],
        "prompt_version": PROMPT_VERSION,
        "git_commit": GIT_COMMIT,
        "slurm_job_ids": version["slurm_job_ids"],
        "blythe_runs": version["blythe_runs"],
        "reviews_by_kind": sections["reviews_by_kind"],
        **tally,
        "reviews_fallback": [d["doc_id"] for d in documents if d.get("fallback")],
        "n_matched_text_top1": same_text,
        "n_matched_heading_top1": same_heading,
        "mean_probability_matched": round(
            sum(r["probability"] or 0 for r in matched) / len(matched), 4
        )
        if matched
        else None,
        "sections_by_template": by_template,
        "seconds_generating": (summary.get("seconds_generating") or 0)
        + (sections["documents_summary"].get("seconds_generating") or 0),
        "prompt_tokens_per_second": run.get("prompt_tokens_per_second"),
        "vllm": run.get("vllm"),
        "gpu": run.get("gpu"),
        "driver": run.get("driver"),
    }
    log(
        f"OK    {tally['n_reviews_matched']} of {tally['n_reviews']} reviews given a template;"
        f" {tally['n_matched']} sections matched, {tally['n_unmatched']} flagged unmatched,"
        f" {tally['n_no_answer']} without an answer"
    )
    log(
        f"OK    Stored in corpus.document_choices and corpus.template_choices for {dataset}"
        f" against {guidance}"
    )
    mlflow_run = log_to_mlflow(result)
    if mlflow_run:
        result["mlflow_run_id"] = mlflow_run
        log(f"OK    Logged to MLflow: run {mlflow_run} in experiment 'template-choices'")
    if tally["n_doubtful"] or tally["n_reviews_doubtful"]:
        result["check_by_hand"] = (
            f"DOUBTFUL: {tally['n_reviews_doubtful']} reviews and {tally['n_doubtful']} sections"
            " (see the Standardised tab's Doubtful list, or corpus.current_template_choices"
            " WHERE doubtful)"
        )
        log(f"DOUBT {result['check_by_hand']}")
    if tally["n_no_answer"]:
        result["note"] = (
            f"{tally['n_no_answer']} sections have no answer (see"
            " template_choices.error); run again to answer them"
        )
    result["log_tail"] = log_tail
    return result


def log_to_mlflow(result: dict) -> str | None:
    if not use_mlflow():
        return None
    import mlflow

    mlflow.set_experiment("template-choices")
    name = f"{result['dataset']} {result['revision'][:8]} {result['guidance']} {result['reviews']}"
    with mlflow.start_run(run_name=name) as run:
        params = {
            key: result[key]
            for key in (
                "dataset",
                "revision",
                "guidance",
                "reviews",
                "model",
                "model_revision",
                "prompt_version",
                "git_commit",
                "vllm",
                "gpu",
                "driver",
            )
        }
        params["slurm_job_ids"] = ",".join(result["slurm_job_ids"])
        params["blythe_runs"] = ",".join(result["blythe_runs"])
        mlflow.log_params(params)
        mlflow.log_metrics(
            {
                k: v
                for k, v in result.items()
                if isinstance(v, (int, float))
                and not isinstance(v, bool)
                and k.startswith(("n_", "mean_", "seconds", "prompt_tokens"))
            }
        )
        for key in ("dataset", "guidance", "reviews", "git_commit"):
            mlflow.set_tag(key, result[key])
    return run.info.run_id


# ---------------------------------------------------------------- the workflow


def new_run_id(dataset: str, guidance: str, reviews: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"o1-choose-{dataset}-{guidance}-{reviews}-{stamp}-{secrets.token_hex(2)}".lower()


async def wait_for_job(
    phase: str, job: dict, check_every_minutes: int, give_up_after_hours: int
) -> dict | None:
    """Waits until SLURM says the job has ended (its last step wakes this run; otherwise a
    check every check_every_minutes). Returns its state, or None after giving up (the job is
    then cancelled)."""
    rounds = max(1, (give_up_after_hours * 60) // max(1, check_every_minutes))
    for i in range(1, rounds + 1):
        woke = await wait_for_approval(
            key=f"{phase}_finished_{i}", timeout=check_every_minutes * 60
        )
        nxt = await step(
            f"{phase}_callback_{i + 1}", lambda i=i: get_approval_urls(f"{phase}_finished_{i + 1}")
        )
        state = await check(job["run_dir"], job["slurm_job_id"], nxt["resume"])
        if state["finished"]:
            return {**state, "checks": i, "woken_by": "Blythe" if woke.get("approved") else "timer"}
    await cancel(job["slurm_job_id"])
    return None


@workflow
async def main(
    dataset: Literal["DHRC-100", "DHRC-RAW"] = "DHRC-100",
    guidance: Literal["dhr-2016", "dardr-2026"] = "dhr-2016",
    reviews: Literal["pilot", "all"] = "pilot",
    model: Literal["Qwen/Qwen3.8-27B-FP8"] = "Qwen/Qwen3.8-27B-FP8",
    hours: int = 0,
    check_every_minutes: int = 30,
    give_up_after_hours: int = 96,
    dry_run: bool = False,
):
    """Windmill runs this; only Rowan's runs can use Blythe. dataset and guidance: whose
    reviews, against which templates (stage 4 must have run for them, which stores the
    templates). reviews: 'pilot' (the 10 pilot reviews, minutes of GPU time) or 'all'.
    model: the LLM on Blythe. hours: the section job's SLURM time limit (0 = worked out from
    the size of its prompts; at most 48). check_every_minutes: how often to check a job that
    hasn't woken this run. give_up_after_hours: when to stop waiting for a job and cancel it.
    dry_run: only build the document prompts and show a sample."""
    check_owner()  # Rowan's Blythe account: no one else may run this, not even a dry run
    check_choices(dataset, guidance, reviews, model)
    if hours and not 1 <= hours <= MAX_HOURS:
        raise ValueError(f"hours must be 0 (automatic) or 1 to {MAX_HOURS}")
    if dry_run:
        return await preview(dataset, guidance, reviews)

    run_id = await step("run_id", lambda: new_run_id(dataset, guidance, reviews))
    urls = await step("documents_callback_1", lambda: get_approval_urls("documents_finished_1"))
    documents_job = await submit_documents(
        dataset, guidance, reviews, model, f"{run_id}-documents", urls["resume"]
    )
    documents_state = await wait_for_job(
        "documents", documents_job, check_every_minutes, give_up_after_hours
    )
    if documents_state is None:
        return {
            "result": f"Gave up waiting for the document job after {give_up_after_hours} h"
            " and cancelled it. Nothing stored.",
            **documents_job,
        }

    urls = await step("sections_callback_1", lambda: get_approval_urls("sections_finished_1"))
    sections = await submit_sections(
        dataset,
        guidance,
        reviews,
        documents_job,
        documents_state,
        hours,
        f"{run_id}-sections",
        urls["resume"],
    )
    sections_state = None
    if sections["sections_job"]:
        sections_state = await wait_for_job(
            "sections", sections["sections_job"], check_every_minutes, give_up_after_hours
        )
        if sections_state is None:
            return {
                "result": f"Gave up waiting for the section job after {give_up_after_hours}"
                " h and cancelled it. Nothing stored.",
                **sections["sections_job"],
            }
    result = await load_results(dataset, guidance, reviews, documents_job, sections, sections_state)
    return {**result, "documents_job": documents_state, "sections_job": sections_state}
