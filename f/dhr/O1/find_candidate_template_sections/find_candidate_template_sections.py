# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#     "httpx==0.28.1",
#     "huggingface-hub==2.4.0",
#     "mlflow-skinny==3.18.0",
#     "numpy==2.5.4",
#     "onnxruntime==1.32.0",
#     "psycopg[binary]==3.3.9",
#     "pypdf==7.1.0",
#     "rapidfuzz==3.14.6",
#     "tokenizers==0.23.3",
#     "wmill==1.875.0",
# ]
# ///
"""O1 stage 4: shortlist candidate template sections for each review section.

Compare normalised headings with fuzzy word scores (0–100), and section text
with guidance text using CPU embeddings (cosine similarity, -1–1). Retain the
top k text matches plus the top k heading matches above the heading threshold.
Store both ranks and scores for every retained candidate.

Read templates from the selected statutory guidance and use the newest grouped
dataset revision. Re-running replaces only that dataset revision's candidates
for that guidance edition. Matching another guidance edition keeps both sets.
This stage produces candidates; choosing a final template section is a later,
unimplemented stage.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from dataclasses import dataclass
from typing import Literal

from ...datasets import DATASETS, check_dataset
from ...db import apply_migrations, connect
from ...settings import ProgressBar, use_mlflow
from ...version import GIT_COMMIT
from .templates import GUIDANCE, GUIDANCES, ensure_templates

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
TEXT_CHARS = 2000  # of a section's own text to read (the model reads ~256 tokens of it)
MAX_TOKENS = 256


class CandidateError(RuntimeError):
    """Something needed to find candidates is missing."""


def log_progress(message: str) -> None:
    """Flush a status line immediately so Windmill can display it during the run."""
    print(message, flush=True)


# Comparing headings.

NUMBER_WORDS = [
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
    "twenty",
]
SMALL_WORDS = {
    "a",
    "an",
    "and",
    "be",
    "by",
    "for",
    "from",
    "in",
    "of",
    "on",
    "page",
    "the",
    "to",
    "with",
}
DASHES = chr(0x2013) + chr(0x2014)  # en and em dashes
RIGHT_QUOTE = chr(0x2019)  # a curly apostrophe
# Numbering at the start of a heading: "3.2", "iv.", "A)", "Two:", "Section 4:", "Part One".
NUMBERING = re.compile(
    r"^(?:(?:section|part|chapter|appendix|annex|annexe)\s+)?"
    r"(?:\d+(?:\.\d+)*[a-z]?|[ivxlc]+(?=[.):\s])|[a-z](?=[.)])"
    # a number word only when a space or a separator follows ("One-off" isn't numbering)
    rf"|(?:{'|'.join(NUMBER_WORDS)})(?=\s|[.:){DASHES}]))"
    rf"[\s.:)\-{DASHES}]*"
)
LEARN = {"learnt", "learned", "learning"}


def heading_words(heading: str) -> str:
    """A heading reduced to the words that say what it is about, for comparing headings:
    numbering, small words and plural endings dropped, lower case. "3.2 Lessons Learned"
    and "LESSONS TO BE LEARNT" both become "lesson learn"."""
    text = heading.lower().replace(RIGHT_QUOTE, "'").replace("&", " and ").strip()
    text = re.sub(r"'s\b", "", text)  # "victim's" -> "victim"
    for _ in range(3):  # "Section 2: 2.1 Timescales" has numbering twice
        shorter = NUMBERING.sub("", text, count=1).strip()
        if shorter == text:
            break
        text = shorter
    words = []
    for word in re.findall(r"[a-z0-9]+", text):
        if word in SMALL_WORDS or word.isdigit():
            continue
        if word in LEARN:
            word = "learn"
        elif len(word) > 4 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        words.append(word)
    return " ".join(words)


def heading_scores(section_headings: list[str], template_headings: list[str]):
    """Return a review-section by template-section matrix of heading scores (0–100).

    Average token-set and token-sort similarity so extra words lower the score
    without discarding partial matches. Score duplicate headings once and map
    the results back to input order. Empty normalised headings always score zero."""
    import numpy as np
    from rapidfuzz import fuzz, process

    section_words = [heading_words(h) for h in section_headings]
    template_words = [heading_words(h) for h in template_headings]
    unique_headings = sorted(set(section_words))
    similarities = (
        process.cdist(
            unique_headings, template_words, scorer=fuzz.token_set_ratio, dtype=np.float32
        )
        + process.cdist(
            unique_headings, template_words, scorer=fuzz.token_sort_ratio, dtype=np.float32
        )
    ) / 2
    similarities[[i for i, words in enumerate(unique_headings) if not words]] = (
        0  # e.g. a heading "1.1"
    )
    heading_indices = {words: i for i, words in enumerate(unique_headings)}
    return (
        similarities[[heading_indices[words] for words in section_words]]
        if section_words
        else similarities[:0]
    )


# Choosing candidates.


def rank_scores(scores):
    """Rank each score within its row, highest first, using 1-based ranks.

    Stable sorting breaks ties in template order, making repeated runs deterministic."""
    import numpy as np

    order = np.argsort(-scores, axis=1, kind="stable")
    result = np.empty_like(order)
    rows = np.arange(scores.shape[0])[:, None]
    result[rows, order] = np.arange(1, scores.shape[1] + 1)
    return result


@dataclass
class Candidate:
    section: int  # index into the sections
    template_section: int  # index into the template sections
    heading_score: float
    heading_rank: int
    text_score: float
    text_rank: int


def select_candidates(
    heading_similarities, text_similarities, k: int, min_heading_score: float
) -> list[Candidate]:
    """Keep top-k text matches and heading matches that meet the score threshold.

    Both inputs are section-by-template score matrices with matching shapes.
    Return their union once per section/template pair, preserving both scores
    and ranks. This shortlists candidates without assigning a final template."""
    import numpy as np

    heading_rank, text_rank = rank_scores(heading_similarities), rank_scores(text_similarities)
    candidate_mask = (text_rank <= k) | (
        (heading_rank <= k) & (heading_similarities >= min_heading_score)
    )
    return [
        Candidate(
            int(section_index),
            int(template_index),
            round(float(heading_similarities[section_index, template_index]), 1),
            int(heading_rank[section_index, template_index]),
            round(float(text_similarities[section_index, template_index]), 4),
            int(text_rank[section_index, template_index]),
        )
        for section_index, template_index in zip(*np.nonzero(candidate_mask), strict=True)
    ]


# Reading and writing.

TEMPLATE_SQL = """
SELECT ts.template, ts.position, ts.heading, ts.guidance
FROM corpus.template_sections AS ts
JOIN corpus.templates AS t USING (template)
WHERE t.guidance = %s AND t.kind <> 'extra sections'  -- step 5's own additions
ORDER BY t.appendix, ts.position
"""
# Removes one dataset version's candidates against one guidance (before storing new ones).
DELETE_SQL = """
DELETE FROM corpus.template_candidates
WHERE revision = %(revision)s
  AND template IN (SELECT template FROM corpus.templates WHERE guidance = %(guidance)s)
"""

# Each section with the start of its own text (not its subsections') and its subsections'
# headings, in reading order.
SECTIONS_SQL = """
WITH own AS (
    SELECT doc_id, section_id,
           left(string_agg(text, chr(10) ORDER BY position), %(chars)s) AS text
    FROM corpus.blocks
    WHERE revision = %(revision)s AND section_id IS NOT NULL
      AND block_type <> 'SectionHeader' AND text <> ''
    GROUP BY doc_id, section_id
), children AS (
    SELECT doc_id, parent_id AS section_id, string_agg(heading, '; ' ORDER BY position) AS headings
    FROM corpus.sections
    WHERE revision = %(revision)s AND parent_id IS NOT NULL
    GROUP BY doc_id, parent_id
)
SELECT s.doc_id, s.section_id, s.heading, coalesce(own.text, ''), coalesce(c.headings, '')
FROM corpus.sections AS s
LEFT JOIN own USING (doc_id, section_id)
LEFT JOIN children AS c USING (doc_id, section_id)
WHERE s.revision = %(revision)s
ORDER BY s.doc_id, s.position
"""


@dataclass
class Section:
    doc_id: str
    section_id: str
    heading: str
    text: str  # the start of its own text
    subsections: str  # its subsections' headings, "; " between them

    def for_embedding(self) -> str:
        parts = [self.heading, self.text]
        if self.subsections:
            parts.append(f"Subsections: {self.subsections}")
        return "\n".join(p for p in parts if p)


def template_text(heading: str, guidance: str) -> str:
    """Combine a template section's heading and guidance for the embedding model."""
    return f"{heading}\n{guidance}" if guidance else heading


def store_candidates(conn, revision: str, sections, template_sections, candidates) -> None:
    """Copy candidate scores and ranks into the current dataset revision's transaction."""
    with (
        conn.cursor() as cursor,
        cursor.copy(
            "COPY corpus.template_candidates (revision, doc_id, section_id, template,"
            " template_position, heading_score, heading_rank, text_score, text_rank) FROM STDIN"
        ) as copy,
    ):
        for candidate in candidates:
            section, (template, position, _, _) = (
                sections[candidate.section],
                template_sections[candidate.template_section],
            )
            copy.write_row(
                (
                    revision,
                    section.doc_id,
                    section.section_id,
                    template,
                    position,
                    candidate.heading_score,
                    candidate.heading_rank,
                    candidate.text_score,
                    candidate.text_rank,
                )
            )


def summarise_match_agreement(sections, template_sections, heading, text) -> dict:
    """Count whether exact heading matches also rank first or in the top three by text."""
    import numpy as np

    names = [heading_words(t[2]) for t in template_sections]
    exact = top1 = top3 = 0
    for i, s in enumerate(sections):
        words = heading_words(s.heading)
        targets = [j for j, name in enumerate(names) if words and name == words]
        if not targets:
            continue
        exact += 1
        best = np.argsort(-text[i], kind="stable")
        top1 += int(best[0] in targets)
        top3 += int(any(j in targets for j in best[:3]))
    return {"n_named_sections": exact, "n_named_text_top1": top1, "n_named_text_top3": top3}


def log_to_mlflow(summary: dict) -> str | None:
    """Records the run in MLflow's 'template-candidates' experiment."""
    if not use_mlflow():
        return None
    import mlflow

    mlflow.set_experiment("template-candidates")
    name = f"{summary['dataset']} {summary['revision'][:8]} {summary['guidance']}"
    with mlflow.start_run(run_name=name) as run:
        mlflow.log_params(
            {
                key: summary[key]
                for key in (
                    "dataset",
                    "revision",
                    "guidance",
                    "model",
                    "k",
                    "min_heading_score",
                    "guidance_sha256",
                    "git_commit",
                )
            }
        )
        mlflow.log_metrics({k: v for k, v in summary.items() if k.startswith(("n_", "seconds"))})
        mlflow.set_tag("git_commit", summary["git_commit"])
        mlflow.set_tag("dataset", summary["dataset"])
        mlflow.set_tag("guidance", summary["guidance"])
    return run.info.run_id


def run(
    dataset: str = "DHRC-100",
    guidance: str = GUIDANCE,
    model: str = DEFAULT_MODEL,
    k: int = 5,
    min_heading_score: float = 60,
) -> dict:
    """Finds candidates for the newest grouped version of a dataset, against one guidance's
    templates. Returns a summary."""
    from .embeddings import Encoder

    check_dataset(dataset)
    if guidance not in GUIDANCES:
        raise CandidateError(f"Unknown guidance {guidance!r}: choose one of {', '.join(GUIDANCES)}")
    if k < 1:
        raise CandidateError("k must be at least 1")
    progress = ProgressBar()
    started = time.time()
    with connect() as conn:
        applied_migrations = apply_migrations(conn)
        if applied_migrations:
            log_progress(f"OK    Database tables updated: {', '.join(applied_migrations)}")
        row = conn.execute(
            "SELECT v.revision FROM corpus.current_versions AS v"
            " JOIN corpus.grouped_versions AS g USING (revision) WHERE v.dataset = %s",
            (dataset,),
        ).fetchone()
        if not row:
            raise CandidateError(
                f"{dataset}'s newest version isn't grouped by section yet:"
                " run f/dhr/O1/group_blocks_by_section/group_blocks_by_section for it first"
            )
        revision = row[0]
        guidance_sha256 = ensure_templates(conn, guidance, log_progress)
        template_sections = conn.execute(TEMPLATE_SQL, (guidance,)).fetchall()
        query = {"revision": revision, "chars": TEXT_CHARS}
        sections = [Section(*row) for row in conn.execute(SECTIONS_SQL, query)]
        if not sections:
            raise CandidateError(f"{dataset} version {revision} has no sections")
        n_templates = len({t[0] for t in template_sections})
        log_progress(
            f"Finding template candidates for {len(sections)} sections of {dataset} version"
            f" {revision}, against {len(template_sections)} sections of the {n_templates} templates"
            f" in the {guidance} guidance..."
        )
        progress(3)

        log_progress("      comparing headings")
        heading_similarities = heading_scores(
            [s.heading for s in sections], [t[2] for t in template_sections]
        )
        progress(5)

        log_progress(f"      comparing text with {model} (each text cut to {MAX_TOKENS} tokens)")
        encoder = Encoder(model, MAX_TOKENS)
        templates_embedded = encoder.encode([template_text(t[2], t[3]) for t in template_sections])
        reported_fraction = [0]

        def report_encoding_progress(fraction: float) -> None:
            progress(5 + 85 * fraction)
            if fraction >= reported_fraction[0] + 0.1 or fraction == 1:
                reported_fraction[0] = fraction
                log_progress(
                    f"      embedded {int(fraction * len(sections))}/{len(sections)} sections"
                )

        sections_embedded = encoder.encode(
            [s.for_embedding() for s in sections], progress=report_encoding_progress
        )
        text_similarities = sections_embedded @ templates_embedded.T
        candidates = select_candidates(
            heading_similarities, text_similarities, k, min_heading_score
        )
        progress(92)

        with conn.transaction():
            conn.execute(
                "DELETE FROM corpus.candidate_versions WHERE revision = %s AND guidance = %s",
                (revision, guidance),
            )
            conn.execute(DELETE_SQL, {"revision": revision, "guidance": guidance})
            store_candidates(conn, revision, sections, template_sections, candidates)
            conn.execute(
                "INSERT INTO corpus.candidate_versions (revision, guidance, model, k,"
                " min_heading_score, git_commit, n_sections, n_candidates)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    revision,
                    guidance,
                    model,
                    k,
                    min_heading_score,
                    GIT_COMMIT,
                    len(sections),
                    len(candidates),
                ),
            )

    by_heading = sum(
        1 for c in candidates if c.heading_rank <= k and c.heading_score >= min_heading_score
    )
    summary = {
        "dataset": dataset,
        "revision": revision,
        "guidance": guidance,
        "model": model,
        "k": k,
        "min_heading_score": min_heading_score,
        "guidance_sha256": guidance_sha256,
        "git_commit": GIT_COMMIT,
        "n_reviews": len({s.doc_id for s in sections}),
        "n_sections": len(sections),
        "n_template_sections": len(template_sections),
        "n_candidates": len(candidates),
        "n_candidates_by_heading": by_heading,
        "n_sections_with_heading_candidate": len(
            {
                c.section
                for c in candidates
                if c.heading_rank <= k and c.heading_score >= min_heading_score
            }
        ),
        **summarise_match_agreement(
            sections, template_sections, heading_similarities, text_similarities
        ),
        "seconds": round(time.time() - started, 1),
    }
    log_progress(
        f"OK    {summary['n_candidates']} candidates for {summary['n_sections']} sections of"
        f" {summary['n_reviews']} reviews ({summary['n_candidates'] / len(sections):.1f} each)"
    )
    log_progress(
        f"OK    {summary['n_sections_with_heading_candidate']} sections have a template heading"
        f" at least {min_heading_score:g}/100 alike their own"
    )
    if summary["n_named_sections"]:
        log_progress(
            f"OK    {summary['n_named_sections']} sections are headed exactly as a template section;"
            f" for {summary['n_named_text_top1']} that is also their best match by text, for"
            f" {summary['n_named_text_top3']} one of their best 3"
        )
    log_progress(
        f"OK    Stored in corpus.template_candidates for {dataset} version {revision}, against"
        f" the {guidance} guidance ({summary['seconds']:.0f} s)"
    )
    run_id = log_to_mlflow(summary)
    if run_id:
        log_progress(f"OK    Logged to MLflow: run {run_id} in experiment 'template-candidates'")
        summary["mlflow_run_id"] = run_id
    return summary


def main(
    dataset: Literal["DHRC-100", "DHRC-RAW"] = "DHRC-100",
    guidance: Literal["dhr-2016", "dardr-2026"] = "dhr-2016",
    model: Literal["BAAI/bge-small-en-v1.5", "BAAI/bge-base-en-v1.5"] = "BAAI/bge-small-en-v1.5",
    k: int = 5,
    min_heading_score: float = 60,
):
    """Windmill runs this. dataset: which dataset's newest grouped version. guidance: whose
    templates to match against, the 2016 DHR guidance or the September 2026 DARDR guidance.
    model: the embedding model for comparing text (bge-base is about 3x slower). k: how many
    candidates each way (heading, text) adds per section. min_heading_score: how alike (0 to
    100) a template heading must be to the section's to count."""
    sys.stdout.reconfigure(line_buffering=True)  # show progress in the log as it happens
    return run(dataset, guidance, model, k, min_heading_score)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", default="DHRC-100", choices=sorted(DATASETS))
    parser.add_argument("--guidance", default=GUIDANCE, choices=sorted(GUIDANCES))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--min-heading-score", type=float, default=60)
    args = parser.parse_args()
    run(args.dataset, args.guidance, args.model, args.k, args.min_heading_score)
    log_progress("Done.")
