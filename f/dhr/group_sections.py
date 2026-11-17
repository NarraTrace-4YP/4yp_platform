# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#     "psycopg[binary]==3.3.9",
#     "mlflow-skinny==3.18.0",
#     "wmill==1.875.0",
# ]
# ///
"""Groups each review's blocks under their sections: O1's hierarchical representation.

Datalab gives a review page by page. This puts its blocks back in reading order and under
the headings they belong to, so a section that runs across a page break is one section
again, and a section can be fetched with everything nested inside it (its subsections
too). Run it in Windmill after loading a dataset: open f/dhr/group_sections, pick the
dataset and click Run. It groups the newest version loaded, replacing any earlier grouping
of that version, so it is always safe to re-run.

Where the structure comes from depends on the dataset (the "headings" setting in
datasets.py):

- DHRC-100 ("checked"): the fields the team validated by hand. Each heading has its level
  (section_level) and the heading it sits under (parent_section_id); every other block
  names the heading it belongs to (containing_section_id).
- DHRC-RAW ("datalab"): Datalab's own guesses, which nobody has checked. Each block's
  section_hierarchy lists the headings it sits under ({level: heading}), so a block
  belongs to the deepest one, and a heading's level is the one it is listed at (or its
  HTML tag, <h2> = level 2, if nothing comes after it).

Either way, a review is only stored if its structure holds together: every block points
to a heading that exists and comes before it, every heading sits under one at a higher
level, and each section is one unbroken stretch of the review (nothing from outside it in
the middle). For DHRC-100 one review that fails stops the run and nothing is stored; for
DHRC-RAW it is left out and listed. Page headers and footers (running titles, "Page 3 of
56") are left out: they are not part of any section.

The results go in corpus.sections and corpus.blocks; corpus.section_blocks(...) and
corpus.section_text(...) fetch a section with everything inside it.

On a laptop, with DHR_DATABASE_URL set:

    uv run python -m f.dhr.group_sections --dataset DHRC-RAW
    uv run python -m f.dhr.group_sections --file review.json  # just print one file's outline
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal

from .datasets import DATASETS, check_dataset
from .db import apply_migrations, connect
from .settings import ProgressBar, use_mlflow
from .version import GIT_COMMIT

HEADING = "SectionHeader"
PAGE_FURNITURE = {"PageHeader", "PageFooter"}  # repeated on every page, part of no section
HEADING_TAG = re.compile(r"\s*<h([1-6])\b", re.IGNORECASE)
MAX_PROBLEMS_SHOWN = 5


class GroupError(RuntimeError):
    """A review's structure doesn't hold together, so it can't be grouped."""


def say(message: str) -> None:
    print(message, flush=True)


# ----------------------------------------------------------------- the words in a block

LINE_BREAKS = {
    "p", "div", "br", "li", "tr", "table", "thead", "tbody", "ul", "ol", "hr",
    "caption", "blockquote", "section", "h1", "h2", "h3", "h4", "h5", "h6",
}  # fmt: skip
CELLS = {"td", "th"}


class _Words(HTMLParser):
    """Collects the text of some HTML, starting a new line where the HTML starts a new
    paragraph, list item or table row, and putting " | " between table cells. Line breaks
    in the HTML source itself are just spaces. Images' descriptions (alt) are kept aside."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.cells_in_row = 0
        self.image_descriptions: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "img":
            alt = dict(attrs).get("alt")
            if alt:
                self.image_descriptions.append(alt)
        elif tag in CELLS:
            if self.cells_in_row:
                self.parts.append(" | ")
            self.cells_in_row += 1
        elif tag in LINE_BREAKS:
            self.parts.append("\n")
            if tag == "tr":
                self.cells_in_row = 0

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in LINE_BREAKS:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(re.sub(r"\s+", " ", data))


def plain_text(html: str | None) -> str:
    """The words in a block's HTML, one paragraph (or list item, or table row) per line.
    A block that is only an image (a signature, a logo) gives the image's description."""
    parser = _Words()
    parser.feed(html or "")
    parser.close()
    lines = []
    for line in "".join(parser.parts).split("\n"):
        line = re.sub(r"\s+", " ", line).strip()
        line = re.sub(r"^(\|\s*)+|(\s*\|)+$", "", line).strip()  # cells left empty at the ends
        if line:
            lines.append(line)
    return "\n".join(lines) or "\n".join(parser.image_descriptions)


# ----------------------------------------------------------------- one review's structure


@dataclass
class Block:
    block_id: str
    position: int  # reading order, from 0
    page: int
    block_type: str
    section_id: str | None  # a heading's is its own; None before the first heading
    html: str
    text: str


@dataclass
class Section:
    section_id: str  # its heading's block id
    parent_id: str | None
    level: int
    heading: str
    position: int
    last_position: int
    first_page: int
    last_page: int
    n_blocks: int  # blocks directly in it, not counting its heading or subsections


@dataclass
class Grouped:
    blocks: list[Block]
    sections: list[Section]
    n_page_furniture: int  # page headers and footers left out
    n_loose_headings: int  # heading blocks outside the hierarchy, kept as ordinary blocks


def reading_order(raw: dict) -> tuple[list[dict], int]:
    """A review's blocks in reading order (page by page, top to bottom), without page
    headers and footers. Also returns how many headers and footers were left out."""
    if not isinstance(raw, dict) or not isinstance(raw.get("children"), list):
        raise GroupError("the review has no pages")
    blocks, furniture = [], 0
    for number, page in enumerate(raw["children"]):
        for block in (page or {}).get("children") or []:
            if block.get("block_type") in PAGE_FURNITURE:
                furniture += 1
                continue
            block = dict(block)
            block.setdefault("page", number)
            blocks.append(block)
    return blocks, furniture


def level_number(value) -> int | None:
    """A heading level as a whole number of at least 1, or None if it isn't one."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def checked_structure(blocks: list[dict]) -> tuple[dict, list, list[str]]:
    """The structure from the fields the team validated (DHRC-100). Returns
    ({heading id: (level, parent id)}, each block's section id, problems)."""
    headings, sections, problems = {}, [], []
    for block in blocks:
        if block["block_type"] == HEADING:
            headings[block["id"]] = (
                level_number(block.get("section_level")),
                block.get("parent_section_id"),
            )
            sections.append(block["id"])
        else:
            if "containing_section_id" not in block:
                problems.append(f"{block['id']} has no containing_section_id")
            sections.append(block.get("containing_section_id"))
    return headings, sections, problems


def datalab_structure(blocks: list[dict]) -> tuple[dict, list, list[str]]:
    """The structure from Datalab's own guesses (DHRC-RAW): each block's section_hierarchy
    ({level: heading id}) lists the headings it sits under. Returns the same as
    checked_structure."""
    headings, sections, problems = {}, [], []
    listed_at = {}  # heading id -> the level later blocks list it at
    trails = []
    for block in blocks:
        try:
            trail = {int(k): v for k, v in (block.get("section_hierarchy") or {}).items()}
        except (TypeError, ValueError, AttributeError):
            problems.append(f"{block['id']} has an unreadable section_hierarchy")
            trail = {}
        trails.append(trail)
        for level, heading_id in trail.items():
            listed_at.setdefault(heading_id, level)
    for block, trail in zip(blocks, trails):
        if block["block_type"] == HEADING:
            # The level section_hierarchy lists it at defines the nesting; the HTML tag is
            # only used for a heading no block lists (one with nothing after it).
            tag = HEADING_TAG.match(block.get("html") or "")
            level = listed_at.get(block["id"]) or (int(tag.group(1)) if tag else None)
            above = {k: v for k, v in trail.items() if level and k < level and v != block["id"]}
            headings[block["id"]] = (level, above[max(above)] if above else None)
            sections.append(block["id"])
        else:
            sections.append(trail[max(trail)] if trail else None)
    return headings, sections, problems


STRUCTURES = {"checked": checked_structure, "datalab": datalab_structure}
HEADINGS_FROM = {
    "checked": "the team's checked headings",
    "datalab": "Datalab's own headings (not checked)",
}


def group(raw: dict, headings_from: str) -> Grouped:
    """Puts one review's blocks under their sections and checks the result holds together.
    Raises GroupError listing what's wrong if it doesn't."""
    blocks, furniture = reading_order(raw)
    headings, sections, problems = STRUCTURES[headings_from](blocks)

    ids = Counter(block["id"] for block in blocks)
    problems += [f"block id {i} is used {n} times" for i, n in ids.items() if n > 1]
    if problems:
        raise GroupError(summarise(problems))
    position = {block["id"]: i for i, block in enumerate(blocks)}

    # A heading with no level that nothing points to is outside the hierarchy (the team left
    # it out, or Datalab never used it): it stays as an ordinary block in the running section.
    pointed_to = {s for b, s in zip(blocks, sections) if s and s != b["id"]}
    pointed_to |= {parent for _, parent in headings.values()}
    loose = {h for h, (level, _) in headings.items() if level is None and h not in pointed_to}
    for i, block in enumerate(blocks):
        if block["id"] in loose:
            if headings_from == "checked" and block.get("containing_section_id"):
                sections[i] = block["containing_section_id"]
            else:
                sections[i] = sections[i - 1] if i else None
    for h in loose:
        del headings[h]

    for heading_id, (level, parent) in headings.items():
        if level is None:
            problems.append(f"heading {heading_id} has no level")
        elif parent is not None:
            if parent not in headings:
                problems.append(f"heading {heading_id} sits under {parent}, which isn't a heading")
            elif position[parent] > position[heading_id]:
                problems.append(f"heading {heading_id} comes before its parent {parent}")
            elif headings[parent][0] is not None and headings[parent][0] >= level:
                problems.append(
                    f"heading {heading_id} (level {level}) sits under {parent}, "
                    f"which is level {headings[parent][0]}"
                )
    for i, (block, section) in enumerate(zip(blocks, sections)):
        if section is None or block["id"] in headings:
            continue
        if section not in headings:
            problems.append(f"{block['id']} belongs to {section}, which isn't a heading")
        elif position[section] > i:
            problems.append(f"{block['id']} comes before its heading {section}")
    if problems:
        raise GroupError(summarise(problems))

    # Every section a block is inside: its own, then each one above that.
    chains: dict[str | None, tuple[str, ...]] = {None: ()}

    def chain(section: str | None) -> tuple[str, ...]:
        if section not in chains:
            chains[section] = (section, *chain(headings[section][1]))
        return chains[section]

    last = {h: position[h] for h in headings}
    for i, section in enumerate(sections):
        for outer in chain(section):
            last[outer] = i
    for heading_id in headings:  # nothing from outside a section may sit in the middle of it
        for i in range(position[heading_id], last[heading_id] + 1):
            if heading_id not in chain(sections[i]):
                problems.append(
                    f"section {heading_id} ({heading_text(blocks[position[heading_id]])[:60]!r}) "
                    f"is interrupted by {blocks[i]['id']}, which belongs to "
                    f"{sections[i] or 'no section'}"
                )
                break
    if problems:
        raise GroupError(summarise(problems))

    stored = [
        Block(
            block_id=block["id"],
            position=i,
            page=int(block["page"]),
            block_type=block["block_type"],
            section_id=sections[i],
            html=block.get("html") or "",
            text=heading_text(block) if block["id"] in headings else plain_text(block.get("html")),
        )
        for i, block in enumerate(blocks)
    ]
    direct = Counter(s for b, s in zip(blocks, sections) if s and b["id"] not in headings)
    grouped = []
    for heading_id in sorted(headings, key=position.get):
        level, parent = headings[heading_id]
        start, end = position[heading_id], last[heading_id]
        pages = [stored[i].page for i in range(start, end + 1)]
        grouped.append(
            Section(
                section_id=heading_id,
                parent_id=parent,
                level=level,
                heading=stored[start].text,
                position=start,
                last_position=end,
                first_page=min(pages),
                last_page=max(pages),
                n_blocks=direct[heading_id],
            )
        )
    return Grouped(stored, grouped, furniture, len(loose))


def heading_text(block: dict) -> str:
    """A heading's words on one line (some headings are split over lines in the HTML)."""
    return " ".join(plain_text(block.get("html")).split("\n"))


def summarise(problems: list[str]) -> str:
    shown = "; ".join(problems[:MAX_PROBLEMS_SHOWN])
    more = len(problems) - MAX_PROBLEMS_SHOWN
    return shown + (f"; and {more} more" if more > 0 else "")


def outline(grouped: Grouped) -> list[str]:
    """The review's sections as an indented list, for reading in a terminal or a log."""
    lines = []
    for s in grouped.sections:
        pages = f"p{s.first_page}" + (f"-{s.last_page}" if s.last_page > s.first_page else "")
        lines.append(f"{'  ' * (s.level - 1)}{s.heading or '(no words)'}  [{pages}, {s.n_blocks}]")
    return lines


# ----------------------------------------------------------------- storing a dataset


@dataclass
class Totals:
    n_documents: int = 0
    n_sections: int = 0
    n_blocks: int = 0
    n_across_pages: int = 0  # sections that cross a page break
    n_before_first_heading: int = 0  # blocks in no section (title page, contents, ...)
    n_page_furniture: int = 0
    n_loose_headings: int = 0

    def add(self, grouped: Grouped) -> None:
        self.n_documents += 1
        self.n_sections += len(grouped.sections)
        self.n_blocks += len(grouped.blocks)
        self.n_across_pages += sum(s.last_page > s.first_page for s in grouped.sections)
        self.n_before_first_heading += sum(b.section_id is None for b in grouped.blocks)
        self.n_page_furniture += grouped.n_page_furniture
        self.n_loose_headings += grouped.n_loose_headings


SECTION_COLUMNS = (
    "revision, doc_id, section_id, parent_id, level, heading, position, last_position,"
    " first_page, last_page, n_blocks"
)
BLOCK_COLUMNS = "revision, doc_id, block_id, position, page, block_type, section_id, html, text"


def write(conn, revision: str, doc_id: str, grouped: Grouped) -> None:
    """Writes one review's sections and blocks (inside the caller's transaction)."""
    with conn.cursor() as cursor:
        with cursor.copy(f"COPY corpus.sections ({SECTION_COLUMNS}) FROM STDIN") as copy:
            for s in grouped.sections:  # in reading order, so a parent is always written first
                copy.write_row(
                    (revision, doc_id, s.section_id, s.parent_id, s.level, s.heading,
                     s.position, s.last_position, s.first_page, s.last_page, s.n_blocks)
                )  # fmt: skip
        with cursor.copy(f"COPY corpus.blocks ({BLOCK_COLUMNS}) FROM STDIN") as copy:
            for b in grouped.blocks:
                copy.write_row(
                    (revision, doc_id, b.block_id, b.position, b.page, b.block_type,
                     b.section_id, b.html, b.text)
                )  # fmt: skip


def group_version(conn, revision: str, headings_from: str, strict: bool, progress) -> dict:
    """Groups every review of one dataset version and stores the result, replacing any
    earlier grouping of it. All or nothing: if anything fails, nothing changes."""
    from psycopg.types.json import Jsonb

    doc_ids = [
        row[0]
        for row in conn.execute(
            "SELECT doc_id FROM corpus.documents WHERE revision = %s ORDER BY doc_id", (revision,)
        )
    ]
    if not doc_ids:
        raise GroupError(f"version {revision} has no reviews stored")
    totals, skipped = Totals(), []
    with conn.transaction():
        conn.execute("DELETE FROM corpus.grouped_versions WHERE revision = %s", (revision,))
        conn.execute("DELETE FROM corpus.blocks WHERE revision = %s", (revision,))
        conn.execute("DELETE FROM corpus.sections WHERE revision = %s", (revision,))
        for number, doc_id in enumerate(doc_ids, start=1):
            (raw,) = conn.execute(
                "SELECT raw FROM corpus.documents WHERE revision = %s AND doc_id = %s",
                (revision, doc_id),
            ).fetchone()
            try:
                grouped = group(raw, headings_from)
            except GroupError as error:
                if strict:
                    raise GroupError(f"{doc_id}: {error}") from error
                skipped.append(f"{doc_id}: {error}")
                continue
            write(conn, revision, doc_id, grouped)
            totals.add(grouped)
            progress(5 + 90 * number / len(doc_ids))
            if number % 100 == 0:
                say(f"      grouped {number}/{len(doc_ids)}")
        conn.execute(
            "INSERT INTO corpus.grouped_versions (revision, headings, git_commit, n_documents,"
            " n_sections, n_blocks, n_skipped, skipped) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (revision, headings_from, GIT_COMMIT, totals.n_documents, totals.n_sections,
             totals.n_blocks, len(skipped), Jsonb(skipped)),
        )  # fmt: skip
    return {**totals.__dict__, "n_skipped": len(skipped), "skipped": skipped}


def log_to_mlflow(summary: dict) -> str | None:
    """Records the grouping as a run in MLflow's 'corpus-sections' experiment."""
    if not use_mlflow():
        return None
    import mlflow

    mlflow.set_experiment("corpus-sections")
    with mlflow.start_run(run_name=f"{summary['dataset']} {summary['revision'][:8]}") as run:
        mlflow.log_params(
            {k: summary[k] for k in ("dataset", "revision", "headings", "git_commit")}
        )
        mlflow.log_metrics({k: v for k, v in summary.items() if k.startswith("n_")})
        mlflow.set_tag("git_commit", summary["git_commit"])
        mlflow.set_tag("dataset", summary["dataset"])
    return run.info.run_id


def run(dataset: str = "DHRC-100") -> dict:
    """Groups the newest version of a dataset. Returns a summary."""
    settings = check_dataset(dataset)
    headings_from = settings["headings"]
    progress = ProgressBar()
    with connect() as conn:
        ran = apply_migrations(conn)
        if ran:
            say(f"OK    Database tables updated: {', '.join(ran)}")
        row = conn.execute(
            "SELECT revision FROM corpus.current_versions WHERE dataset = %s", (dataset,)
        ).fetchone()
        if not row:
            raise GroupError(f"{dataset} isn't loaded yet: run f/dhr/load_dhrc for it first")
        revision = row[0]
        say(
            f"Grouping {dataset} version {revision} by section, using"
            f" {HEADINGS_FROM[headings_from]}..."
        )
        progress(5)
        totals = group_version(conn, revision, headings_from, settings["strict"], progress)

    say(
        f"OK    {totals['n_documents']} reviews: {totals['n_sections']} sections,"
        f" {totals['n_blocks']} blocks; {totals['n_across_pages']} sections cross a page break"
    )
    say(
        f"OK    {totals['n_before_first_heading']} blocks come before the first heading"
        f" (title pages, contents); {totals['n_page_furniture']} page headers and footers left out"
    )
    if totals["n_loose_headings"]:
        say(f"NOTE  {totals['n_loose_headings']} heading blocks sit outside the hierarchy;"
            " kept as ordinary blocks")  # fmt: skip
    if totals["skipped"]:
        say(f"NOTE  Left out {totals['n_skipped']} review(s) whose headings don't fit together:")
        for problem in totals["skipped"][:50]:
            say(f"        {problem}")
    say(f"OK    Stored in corpus.sections and corpus.blocks as {dataset} version {revision}")

    summary = {"dataset": dataset, "revision": revision, "headings": headings_from,
               "git_commit": GIT_COMMIT, **totals}  # fmt: skip
    run_id = log_to_mlflow(summary)
    if run_id:
        say(f"OK    Logged to MLflow: run {run_id} in experiment 'corpus-sections'")
        summary["mlflow_run_id"] = run_id
    return summary


def main(dataset: Literal["DHRC-100", "DHRC-RAW"] = "DHRC-100"):
    """Windmill runs this. dataset: which dataset's newest version to group."""
    sys.stdout.reconfigure(line_buffering=True)  # show progress in the log as it happens
    return run(dataset)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", default="DHRC-100", choices=sorted(DATASETS))
    parser.add_argument("--file", type=Path, help="print one review file's outline instead")
    parser.add_argument(
        "--headings", choices=sorted(STRUCTURES), help="with --file: checked or datalab"
    )
    args = parser.parse_args()
    if args.file:
        headings_from = args.headings or check_dataset(args.dataset)["headings"]
        result = group(json.loads(args.file.read_text(encoding="utf-8")), headings_from)
        print("\n".join(outline(result)))
        print(f"{len(result.sections)} sections, {len(result.blocks)} blocks")
    else:
        run(args.dataset)
        say("Done.")
