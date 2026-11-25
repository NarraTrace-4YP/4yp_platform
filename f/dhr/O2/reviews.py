"""O2 stages 1-4, shared: which reviews a wiki gets, and each review as Markdown.

A review comes from O1's grouped blocks (corpus.current_blocks and corpus.current_sections),
in reading order: each heading at its own depth, then the text, lists, tables (as HTML) and
footnotes. Running page headers and footers are already gone (O1 stage 3). The table of
contents is left out, because it repeats the headings with page numbers.

A paragraph cut in two by a page break is joined back into one paragraph (Rowan, 17 Nov
2026: O1 keeps the halves apart, and the wiki stage treats them as one). The halves are
found as a text block that stops without closing punctuation, followed, on a later page and
past any footnotes, by a text block starting in lower case. Footnotes that sat between the
halves (at the foot of the first page) go straight after the joined paragraph.

With a guidance chosen (template_guidance, default dhr-2016), O1 stage 5's mapping is added
too: the review's template at the top, and under each heading the template section it was
mapped to (when it differs from the one before), so the wiki sees O1's standardised structure as well as the original headings.

A review longer than WeKnora's wiki reads (32,768 characters) is cut into parts at its
headings (split_into_parts, review_parts). Fix, 25 Nov 2026, problem review "O2 wiki reads
only the start of each review": 97 of the 100 DHRC-100 reviews were cut off by WeKnora,
which reads only the start of each file; each part now says which review, part and section
it continues, so it reads on its own (f/dhr/O2/kb_settings.py has the details).

Nothing here reads the evaluation schema: the wiki never sees the case metadata.
"""

from __future__ import annotations

import hashlib
import re

DATASET_CHOICES = ("DHRC-100", "DHRC-RAW", "both")
GUIDANCE_CHOICES = ("dhr-2016", "dardr-2026", "none")
GUIDANCE_NAMES = {"dhr-2016": "2016 guidance", "dardr-2026": "2026 guidance"}
REVIEW_CHOICES = ("pilot", "all", "chosen")

# The specification's pilot set (section 5.2): the same 10 reviews through every stage
# (the same list as O1 stage 5's PILOT_REVIEWS).
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

SKIPPED_TYPES = {"TableOfContents"}
TEXT_TYPES = {"Text"}
ENDS_A_SENTENCE = re.compile(r"""[.!?:;"”’')\]]\s*$""")
STARTS_IN_LOWER_CASE = re.compile(r"^\s*[a-z(,;]")


def check_choices(
    dataset: str, reviews: str, doc_ids: list[str] | None, template_guidance: str = "none"
) -> None:
    if template_guidance not in GUIDANCE_CHOICES:
        raise ValueError(
            f"template_guidance must be one of {', '.join(GUIDANCE_CHOICES)}, not {template_guidance!r}"
        )
    if dataset not in DATASET_CHOICES:
        raise ValueError(f"dataset must be one of {', '.join(DATASET_CHOICES)}, not {dataset!r}")
    if reviews not in REVIEW_CHOICES:
        raise ValueError(f"reviews must be one of {', '.join(REVIEW_CHOICES)}, not {reviews!r}")
    if reviews == "chosen" and not doc_ids:
        raise ValueError("reviews is 'chosen' but no doc_ids were given")


def choose_reviews(
    conn, dataset: str, reviews: str, doc_ids: list[str] | None = None
) -> list[dict]:
    """The reviews to upload, one per doc_id: [{doc_id, dataset, revision}], by doc_id.

    "both" takes every DHRC-100 review (its checked copy) plus the DHRC-RAW reviews that
    aren't in DHRC-100, so no review is in the wiki twice. Only grouped versions count
    (O1 stage 3 must have run). Raises if a chosen or pilot review isn't there.
    """
    check_choices(dataset, reviews, doc_ids)
    rows = conn.execute(
        """
        SELECT DISTINCT b.doc_id, b.dataset, b.revision
        FROM corpus.current_blocks AS b
        JOIN corpus.grouped_versions AS g USING (revision)
        WHERE b.dataset = ANY(%s)
        """,
        (["DHRC-100", "DHRC-RAW"] if dataset == "both" else [dataset],),
    ).fetchall()
    found: dict[str, dict] = {}
    for doc_id, ds, revision in sorted(rows, key=lambda r: (r[0], r[1] != "DHRC-100")):
        found.setdefault(doc_id, {"doc_id": doc_id, "dataset": ds, "revision": revision})
    if reviews == "all":
        wanted = sorted(found)
    else:
        wanted = list(dict.fromkeys(PILOT_REVIEWS if reviews == "pilot" else doc_ids or []))
        missing = [d for d in wanted if d not in found]
        if missing:
            raise ValueError(
                f"{len(missing)} review(s) aren't in {dataset} (grouped by O1 stage 3):"
                f" {', '.join(missing[:10])}"
            )
    if not wanted:
        raise ValueError(f"No grouped reviews in {dataset}: run O1 first.")
    return [found[d] for d in wanted]


def load_review(conn, revision: str, doc_id: str) -> tuple[list[dict], dict[str, int]]:
    """A review's blocks in reading order, and the depth of each of its sections."""
    blocks = [
        dict(zip(("block_id", "position", "page", "block_type", "section_id", "html", "text"), r))
        for r in conn.execute(
            """
            SELECT block_id, position, page, block_type, section_id, html, text
            FROM corpus.blocks WHERE revision = %s AND doc_id = %s ORDER BY position
            """,
            (revision, doc_id),
        ).fetchall()
    ]
    levels = dict(
        conn.execute(
            "SELECT section_id, level FROM corpus.sections WHERE revision = %s AND doc_id = %s",
            (revision, doc_id),
        ).fetchall()
    )
    if not blocks:
        raise ValueError(f"{doc_id} has no blocks in revision {revision}")
    return blocks, levels


def join_split_paragraphs(blocks: list[dict]) -> tuple[list[dict], int]:
    """Joins paragraphs cut by a page break; footnotes between the halves follow the
    joined paragraph. Returns the new block list and how many joins were made."""
    out: list[dict] = []
    joins = 0
    i = 0
    while i < len(blocks):
        block = dict(blocks[i])
        i += 1
        if block["block_type"] not in TEXT_TYPES:
            out.append(block)
            continue
        moved: list[dict] = []
        while True:
            j = i
            between: list[dict] = []
            while j < len(blocks) and blocks[j]["block_type"] == "Footnote":
                between.append(blocks[j])
                j += 1
            if (
                j < len(blocks)
                and blocks[j]["block_type"] in TEXT_TYPES
                and blocks[j]["page"] > block["page"]
                and blocks[j]["section_id"] == block["section_id"]
                and block["text"].strip()
                and not ENDS_A_SENTENCE.search(block["text"])
                and STARTS_IN_LOWER_CASE.match(blocks[j]["text"])
            ):
                nxt = blocks[j]
                block["text"] = block["text"].rstrip() + " " + nxt["text"].lstrip()
                block["page"] = nxt["page"]  # so a paragraph over three pages joins too
                moved.extend(between)
                joins += 1
                i = j + 1
                continue
            break
        out.append(block)
        out.extend(moved)
    return out, joins


def load_template_labels(conn, revision: str, doc_id: str, guidance: str) -> dict:
    """O1 stage 5's mapping for one review: {"document": template title or None,
    "sections": {section_id: template heading}} (matched sections only)."""
    if guidance == "none":
        return {"document": None, "sections": {}}
    rows = conn.execute(
        """
        SELECT section_id, status, template_heading, template_title, document_template_title
        FROM corpus.current_template_choices
        WHERE revision = %s AND doc_id = %s AND guidance = %s
        """,
        (revision, doc_id, guidance),
    ).fetchall()
    sections = {}
    document = None
    for section_id, status, heading, title, document_title in rows:
        document = document or document_title
        if status == "matched" and heading:
            sections[section_id] = (
                heading if title == document_title or not title else f"{title}: {heading}"
            )
    return {"document": document, "sections": sections}


def render_pieces(
    doc_id: str,
    blocks: list[dict],
    levels: dict[str, int],
    labels: dict | None = None,
    guidance: str = "none",
) -> tuple[list[str], list[dict], int]:
    """The review as Markdown pieces: (header lines, body pieces, paragraphs joined).

    Each body piece is {"text", "depth" (a heading's level, 0 for anything else),
    "heading" (the heading's own text, for headings), "label" (the template section in force
    after it)}. render_markdown joins them; split_into_parts packs them into parts."""
    blocks, joins = join_split_paragraphs(blocks)
    labels = labels or {"document": None, "sections": {}}
    name = GUIDANCE_NAMES.get(guidance, guidance)
    header = []
    if labels["document"]:
        header.append(f"Report template ({name}): {labels['document']}")
    pieces: list[dict] = []
    last_label = None
    for block in blocks:
        kind, text = block["block_type"], (block["text"] or "").strip()
        if kind in SKIPPED_TYPES or not (text or block["html"]):
            continue
        if kind == "SectionHeader":
            depth = min(levels.get(block["section_id"], 1) + 1, 6)
            heading = " ".join(text.split())
            piece = f"{'#' * depth} {heading}"
            mapped = labels["sections"].get(block["section_id"])
            # A subsection mapped like its parent says nothing new.
            if mapped and mapped != last_label:
                piece += f"\n\nTemplate section ({name}): {mapped}"
            last_label = mapped or last_label
            pieces.append({"text": piece, "depth": depth, "heading": heading, "label": last_label})
            continue
        if kind == "Table":
            piece = block["html"].strip()
        elif kind == "Footnote":
            piece = f"Footnote: {text}"
        elif kind in ("Picture", "Figure"):
            piece = f"[{kind}: {text}]"
        else:
            piece = text
        pieces.append({"text": piece, "depth": 0, "heading": None, "label": last_label})
    return header, pieces, joins


def render_markdown(
    doc_id: str,
    blocks: list[dict],
    levels: dict[str, int],
    labels: dict | None = None,
    guidance: str = "none",
) -> tuple[str, int]:
    """The review as one Markdown file, and how many split paragraphs were joined."""
    header, pieces, joins = render_pieces(doc_id, blocks, levels, labels, guidance)
    parts = [f"# {doc_id}", *header, *(p["text"] for p in pieces)]
    return "\n\n".join(parts) + "\n", joins


def _hard_split(text: str, limit: int) -> list[str]:
    """A piece longer than a whole part (a huge table, say): cut at line ends, else anywhere."""
    out = []
    while len(text) > limit:
        cut = text.rfind("\n", limit // 2, limit)
        cut = cut if cut > 0 else limit
        out.append(text[:cut])
        text = text[cut:].lstrip("\n")
    if text:
        out.append(text)
    return out


def split_into_parts(
    doc_id: str, header: list[str], pieces: list[dict], part_chars: int, guidance: str = "none"
) -> list[str]:
    """Packs the pieces into parts of at most part_chars characters each, for WeKnora's wiki,
    which reads only the first 32,768 characters of a file (f/dhr/O2/kb_settings.py).

    A part ends before a heading wherever it can: a new part starts at the first heading
    past half the limit that would not fit, otherwise at the piece that would not fit. Every
    part starts with the review's name, "part i of n", the report template, and, when it
    starts inside a section, the headings it sits under and the template section in force,
    so each part can be read on its own."""
    name = GUIDANCE_NAMES.get(guidance, guidance)
    # Room for the part's own header lines; the longest a heading trail is likely to be.
    budget = part_chars - 600 - sum(len(h) + 2 for h in header) - len(doc_id)
    budget = max(budget, part_chars // 2)

    units: list[dict] = []
    for p in pieces:
        if len(p["text"]) > budget:
            units += [
                {**p, "text": t, "depth": p["depth"] if i == 0 else 0}
                for i, t in enumerate(_hard_split(p["text"], budget))
            ]
        else:
            units.append(p)

    groups: list[list[dict]] = []
    current: list[dict] = []
    size = 0
    for u in units:
        n = len(u["text"]) + 2
        if current and size + n > budget:
            # Prefer to end the part before the last heading that is past half the budget.
            cut = None
            running = 0
            for i, c in enumerate(current):
                if c["depth"] and i > 0 and running >= budget // 2:
                    cut = i
                running += len(c["text"]) + 2
            if cut is not None:
                groups.append(current[:cut])
                current = current[cut:]
                size = sum(len(c["text"]) + 2 for c in current)
            if current and size + n > budget:
                groups.append(current)
                current, size = [], 0
        current.append(u)
        size += n
    if current:
        groups.append(current)

    total = len(groups)
    out = []
    trail: dict[int, str] = {}  # depth -> heading text, for the headings above a part's start
    label = None
    for i, group in enumerate(groups, start=1):
        lines = [f"# {doc_id} (part {i} of {total})" if total > 1 else f"# {doc_id}", *header]
        if i > 1 and not group[0]["depth"]:
            above = [trail[d] for d in sorted(trail)]
            if above:
                lines.append("Continues the section: " + " > ".join(above))
            if label:
                lines.append(f"Template section ({name}): {label}")
        elif i > 1 and group[0]["depth"]:
            above = [trail[d] for d in sorted(trail) if d < group[0]["depth"]]
            if above:
                lines.append("Under: " + " > ".join(above))
        for u in group:
            if u["depth"] and u["heading"]:
                trail = {d: h for d, h in trail.items() if d < u["depth"]}
                trail[u["depth"]] = u["heading"]
            label = u["label"]
        out.append("\n\n".join([*lines, *(u["text"] for u in group)]) + "\n")
    return out


def review_parts(
    conn, review: dict, template_guidance: str = "none", part_chars: int = 0
) -> list[dict]:
    """The review ready to upload, as one file (part_chars 0) or as parts under part_chars
    characters each. Each: {doc_id, dataset, revision, part, n_parts, file_name, text,
    sha256, n_chars, n_joined, n_template_labels}."""
    blocks, levels = load_review(conn, review["revision"], review["doc_id"])
    labels = load_template_labels(conn, review["revision"], review["doc_id"], template_guidance)
    if part_chars:
        header, pieces, joins = render_pieces(
            review["doc_id"], blocks, levels, labels, template_guidance
        )
        texts = split_into_parts(review["doc_id"], header, pieces, part_chars, template_guidance)
    else:
        text, joins = render_markdown(review["doc_id"], blocks, levels, labels, template_guidance)
        texts = [text]
    n = len(texts)
    return [
        {
            **review,
            "part": i,
            "n_parts": n,
            "file_name": f"{review['doc_id']}.md"
            if n == 1
            else f"{review['doc_id']} (part {i} of {n}).md",
            "text": text,
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "n_chars": len(text),
            "n_joined": joins,
            "n_template_labels": len(labels["sections"]),
        }
        for i, text in enumerate(texts, start=1)
    ]


def review_file(conn, review: dict, template_guidance: str = "none") -> dict:
    """One review as a single file (no parts)."""
    return review_parts(conn, review, template_guidance, 0)[0]
