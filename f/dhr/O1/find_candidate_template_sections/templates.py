"""Read statutory report templates from the supported GOV.UK guidance editions.

Each guidance specification identifies source files, template boundaries, and
expected section headings. Parse PDF or Word text against that specification
and fail if a required heading is missing or an unexpected heading appears.
Keep source checksums so every stored template can be traced to its inputs.

Downloading unchanged files reuses the stored templates. Changed sources replace
that guidance's templates and invalidate their existing candidate matches.
"""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from dataclasses import dataclass, field
from xml.etree import ElementTree

IMR = "individual management review"
BULLETS = (chr(0xF0B7), chr(0x2022))  # how bullet points come out of the PDFs as text
RIGHT_QUOTE = chr(0x2019)  # a curly apostrophe
DASHES = (chr(0x2013), chr(0x2014))  # en and em dashes
ACRONYMS = {"dardr": "DARDR", "csp": "CSP", "imr": "IMR"}


@dataclass(frozen=True)
class TemplateSpec:
    """One template: its id, the file it's read from (a key of its guidance's sources), the
    line that starts it, its title line if separate (else None), the title to store, the
    kind of report, and its sections in order. Each section is (heading as printed, or None
    if it has none; name to store it under, or None to make one from the printed heading).
    """

    key: str
    source: str
    start: str
    title_line: str | None
    title: str
    kind: str
    sections: tuple[tuple[str | None, str | None], ...]
    numbered: bool = False  # headings may start with a number ("1 LIST OF CONTENTS PAGE")
    text_lines: frozenset[str] = frozenset()  # lines in capitals that are text, not headings
    skip_lines: frozenset[str] = frozenset()  # lines that belong to no section (dividers)
    text_first: bool = False  # text before the first heading goes with the first section
    whole_text: bool = False  # no headings: all its text is one section (a table)
    stop_at: str | None = None  # where the template ends, if something else follows it


@dataclass(frozen=True)
class GuidanceSpec:
    key: str
    title: str
    sources: dict[str, str]  # name: GOV.UK download link
    boundary: str  # a line that starts a new appendix or annex (where a template ends)
    templates: tuple[TemplateSpec, ...]


GUIDANCE_2016_PDF = (
    "https://assets.publishing.service.gov.uk/media/5a80be88e5274a2e87dbb923/"
    "DHR-Statutory-Guidance-161206.pdf"
)
GUIDANCE_2026_PDF = (
    "https://assets.publishing.service.gov.uk/media/6aaa9fdd086ce7c57b81859e/"
    "Domestic_abuse_related_death_review_statutory_guidance1609.pdf"
)
ANNEX_A_2026_DOCX = (
    "https://assets.publishing.service.gov.uk/media/6a9fb119f36e1f225ecb7eca/"
    "_final__Annex_A_-_DARDR_Template.docx"
)

DHR_2016 = GuidanceSpec(
    key="dhr-2016",
    title="Multi-agency Statutory Guidance for the Conduct of Domestic Homicide Reviews (2016)",
    sources={"guidance": GUIDANCE_2016_PDF},
    boundary=r"APPENDIX (ONE|TWO|THREE|FOUR|FIVE)$",
    templates=(
        TemplateSpec(
            key="dhr-2016/imr-outline",
            source="guidance",
            start="APPENDIX ONE",
            title_line="OUTLINE FORMAT FOR INDIVIDUAL MANAGEMENT REVIEWS",
            title="Outline format for individual management reviews",
            kind=IMR,
            sections=(
                ("AGENCY INVOLVEMENT WITH THE VICTIM, THE PERPETRATOR AND THEIR FAMILIES", None),
                ("ANALYSIS OF INVOLVEMENT", None),
            ),
        ),
        TemplateSpec(
            key="dhr-2016/imr",
            source="guidance",
            start="APPENDIX TWO",
            title_line="INDIVIDUAL MANAGEMENT REVIEW TEMPLATE",
            title="Individual management review template",
            kind=IMR,
            sections=(
                ("INTRODUCTION", None),
                ("VICTIM, PERPETRATOR, FAMILY DETAILS IF RELEVANT", None),
                ("TERMS OF REFERENCE", None),
                ("METHODOLOGY", None),
                ("DETAILS OF PARALLEL REVIEWS/PROCESSES", None),
                ("CHRONOLOGY OF AGENCY INVOLVEMENT", None),
                ("ANALYSIS OF INVOLVEMENT", None),
                ("ADDRESSING TERMS OF REFERENCE", None),
                ("EFFECTIVE PRACTICE/LESSONS LEARNT", None),
                ("RECOMMENDATIONS", None),
            ),
            # The question the chronology section asks, printed in capitals under its heading.
            text_lines=frozenset({"WHAT WAS YOUR AGENCY'S INVOLVEMENT WITH THE VICTIM?"}),
        ),
        TemplateSpec(
            key="dhr-2016/overview-report",
            source="guidance",
            start="APPENDIX THREE",
            title_line="OVERVIEW REPORT TEMPLATE",
            title="Overview report template",
            kind="overview report",
            sections=(
                ("TITLE PAGE OF OVERVIEW REPORT", None),
                ("LIST OF CONTENTS PAGE", None),
                (None, "Introduction"),  # the text after the contents page, with no heading
                ("TIMESCALES", None),
                ("CONFIDENTIALITY", None),
                ("TERMS OF REFERENCE", None),
                ("METHODOLOGY", None),
                (
                    (
                        "INVOLVEMENT OF FAMILY, FRIENDS, WORK COLLEAGUES, NEIGHBOURS AND WIDER"
                        " COMMUNITY"
                    ),
                    None,
                ),
                ("CONTRIBUTORS TO THE REVIEW", None),
                ("THE REVIEW PANEL MEMBERS", None),
                ("AUTHOR OF THE OVERVIEW REPORT", None),
                ("PARALLEL REVIEWS", None),
                ("EQUALITY AND DIVERSITY", None),
                ("DISSEMINATION", None),
                ("BACKGROUND INFORMATION (THE FACTS)", None),
                ("CHRONOLOGY", None),
                ("OVERVIEW", None),
                ("ANALYSIS", None),
                ("CONCLUSIONS", None),
                ("LESSONS TO BE LEARNT", None),
                ("RECOMMENDATIONS", None),
            ),
        ),
        TemplateSpec(
            key="dhr-2016/executive-summary",
            source="guidance",
            start="APPENDIX FOUR",
            title_line="EXECUTIVE SUMMARY TEMPLATE",
            title="Executive summary template",
            kind="executive summary",
            sections=(
                ("TITLE PAGE OF EXECUTIVE SUMMARY", None),
                ("LIST OF CONTENTS PAGE", None),
                ("THE REVIEW PROCESS", None),
                ("CONTRIBUTORS TO THE REVIEW", None),
                ("THE REVIEW PANEL MEMBERS", None),
                ("AUTHOR OF THE OVERVIEW REPORT", None),
                ("TERMS OF REFERENCE FOR THE REVIEW", None),
                ("SUMMARY CHRONOLOGY", None),
                ("KEY ISSUES ARISING FROM THE REVIEW", None),
                ("CONCLUSIONS", None),
                ("LESSONS TO BE LEARNED", None),
                ("RECOMMENDATIONS FROM THE REVIEW", None),
            ),
            # Appendix Four ends with a Home Office data collection form, not part of a report.
            stop_at="THIS IS NOT FOR PUBLICATION",
        ),
        TemplateSpec(
            key="dhr-2016/action-plan",
            source="guidance",
            start="APPENDIX FIVE",
            title_line="ACTION PLAN EXAMPLE",
            title="Action plan example",
            kind="action plan",
            sections=((None, "Action plan"),),
            whole_text=True,  # one table, with no headings
        ),
    ),
)

DARDR_2026 = GuidanceSpec(
    key="dardr-2026",
    title="Domestic Abuse Related Death Review: Statutory Guidance (September 2026)",
    sources={"guidance": GUIDANCE_2026_PDF, "annex-a": ANNEX_A_2026_DOCX},
    boundary=r"Annex [A-Z]: ",
    templates=(
        TemplateSpec(
            key="dardr-2026/dardr",
            source="annex-a",
            start="Annex A: DARDR Template",
            title_line=None,
            title="DARDR template",
            kind="overview report",
            numbered=True,
            sections=(
                ("TITLE PAGE OF DARDR", None),
                ("LIST OF CONTENTS PAGE", None),
                ("PEN PORTRAIT OF VICTIM", None),
                # printed "DARDR CHAIR + CSP CONDOLONCES"
                ("DARDR CHAIR + CSP CONDOLONCES", "DARDR Chair and CSP condolences"),
                ("CONFIDENTIALITY AND ANONYMITY STATEMENT", None),
                ("EXECUTIVE SUMMARY", None),
                ("TERMS OF REFERENCE", None),
                ("BACKGROUND INFORMATION (THE FACTS)", None),
                ("AGENCY OVERVIEW", None),
                ("COMBINED CHRONOLOGY", None),
                ("ANALYSIS", None),
                ("CONCLUSIONS", None),
                ("LESSONS LEARNT AND RECOMMENDATIONS", None),
                ("TIMESCALES", None),
                ("CONTRIBUTORS TO THE DARDR", None),
                ("THE DARDR PANEL MEMBERS", None),
                ("DARDR AUTHOR", None),
                ("PARALLEL REVIEWS", None),
                ("SCOPING REVIEW", None),
            ),
            # The divider before the sections on how the review was done (13 onwards).
            skip_lines=frozenset({"ANNEX A: DARDR PROCESS INFORMATION"}),
        ),
        TemplateSpec(
            key="dardr-2026/action-plan",
            source="guidance",
            start="Annex B: Action Plan Template and Guidance",
            title_line=None,
            title="Action plan template and guidance",
            kind="action plan",
            sections=((None, "Action plan"),),
            whole_text=True,  # a table (its column names are in capitals), then guidance
        ),
        TemplateSpec(
            key="dardr-2026/scoping-review",
            source="guidance",
            start="Annex C: Scoping Review Template",
            title_line=None,
            title="Scoping review template",
            kind="scoping review",
            numbered=True,
            sections=(
                ("TITLE PAGE OF SCOPING REVIEW", None),
                (
                    "How was the CSP notified of this death and on what date did this take place?",
                    None,
                ),
                ("Summary of fatal incident", None),
                ("Background - what has been learnt about the victim and perpetrator?", None),
                (
                    "What is the early learning that has been identified by agencies in this case?",
                    None,
                ),
                (
                    (
                        "What action(s), with outcomes and timelines will be taken as a result of"
                        " the learning identified?"
                    ),
                    None,
                ),
                (
                    (
                        "If the Scoping Review Panel has agreed to not commission a DARDR for this"
                        " case please outline the rationale for this decision below."
                    ),
                    None,
                ),
                (
                    "What are the family/friends' views on whether a DARDR should be commissioned?",
                    None,
                ),
                (
                    (
                        "Which agencies and organisations have been consulted/involved in the"
                        " Scoping Review?"
                    ),
                    None,
                ),
                ("Are there other reviews planned relating to this death?", None),
            ),
        ),
        TemplateSpec(
            key="dardr-2026/imr",
            source="guidance",
            start="Annex D: Independent Management Report Template",
            title_line=None,
            title="Independent management report template",
            kind=IMR,
            sections=(
                ("INTRODUCTION", None),
                ("VICTIM, PERPETRATOR, FAMILY DETAILS IF RELEVANT", None),
                ("SUMMARY OF AGENCY INVOLVEMENT", None),
                ("ANALYSIS OF INVOLVEMENT", None),
                ("ADDRESSING TERMS OF REFERENCE", None),
                ("EFFECTIVE PRACTICE, LESSONS LEARNT & RECOMMENDATIONS", None),
            ),
            # A handling note and the IMR's details (name, dates, author) come before its
            # first heading.
            text_first=True,
        ),
    ),
)

GUIDANCES = {g.key: g for g in (DHR_2016, DARDR_2026)}
GUIDANCE = DHR_2016.key  # the default: the guidance the reviews were written under


class TemplateError(RuntimeError):
    """A guidance file doesn't match the templates listed for it."""


@dataclass
class TemplateSection:
    position: int  # order in its template, from 1
    heading: str  # e.g. "Timescales"
    printed: bool  # False: the file gives it no heading (see the module docstring)
    guidance: str  # what the template says goes there ("" if it says nothing)
    page: int | None  # page number as printed (None in a Word file, which has no pages)


@dataclass
class Template:
    key: str
    title: str  # e.g. "Overview report template"
    kind: str
    source_url: str  # the file it was read from
    sha256: str  # of that file
    first_page: int | None
    last_page: int | None
    sections: list[TemplateSection] = field(default_factory=list)


@dataclass
class Guidance:
    key: str
    sha256: str  # of its files together (changes if any of them changes)
    n_pages: int  # of its main document
    templates: list[Template]


Lines = list[tuple[int | None, str]]  # (page number or None, line of text)


def normalise_line(line: str) -> str:
    """Collapse whitespace and normalise apostrophes and dashes before heading comparison."""
    line = line.replace(RIGHT_QUOTE, "'")
    for dash in DASHES:
        line = line.replace(dash, "-")
    return re.sub(r"\s+", " ", line).strip()


def is_uppercase_heading(line: str) -> bool:
    """Require at least two letters before treating an uppercase line as a heading."""
    letters = [c for c in line if c.isalpha()]
    return len(letters) >= 2 and line == line.upper()


def heading_name(printed: str) -> str:
    """A heading printed in capitals, in sentence case, keeping DARDR, CSP and IMR."""
    if not is_uppercase_heading(printed):
        return printed
    text = re.sub(r"\b[a-z]+\b", lambda m: ACRONYMS.get(m.group(0), m.group(0)), printed.lower())
    return text[:1].upper() + text[1:]


def strip_heading_number(line: str) -> str:
    """Remove an integer prefix followed by whitespace from a numbered heading."""
    return re.sub(r"^\d+\s+", "", line)


class TemplateTextBuilder:
    """Collects one template section's text: lines of one paragraph are joined with spaces,
    a blank line ends a paragraph, and each bullet point starts a new line."""

    def __init__(self) -> None:
        self.paragraphs: list[str] = []
        self.open = False

    def add(self, line: str) -> None:
        bullet = line.startswith(BULLETS)
        if bullet:
            line = "- " + line.lstrip("".join(BULLETS)).strip()
        if self.open and not bullet:
            self.paragraphs[-1] += " " + line
        else:
            self.paragraphs.append(line)
        self.open = True

    def end_paragraph(self) -> None:
        self.open = False

    def __str__(self) -> str:
        return "\n".join(self.paragraphs)


def split_sections(spec: TemplateSpec, lines: Lines) -> list[TemplateSection]:
    """Splits one template's lines (page, text), after its title, into the sections the
    spec lists, keeping the text under each heading. Raises TemplateError if a listed
    heading is missing or a heading appears that isn't listed."""
    label = spec.title
    expected = list(spec.sections)
    text_lines = {normalise_line(line) for line in spec.text_lines}
    skip_lines = {normalise_line(line) for line in spec.skip_lines}
    sections: list[TemplateSection] = []
    texts: list[TemplateTextBuilder] = []
    before_first = TemplateTextBuilder()  # text before the first heading (if text_first)
    pending: list[str] = []  # the lines so far of a heading printed over several lines
    pending_page: int | None = None

    def start(page: int | None) -> None:
        printed, name = expected[len(sections)]
        heading = name or heading_name(normalise_line(printed or ""))
        sections.append(TemplateSection(len(sections) + 1, heading, printed is not None, "", page))
        texts.append(TemplateTextBuilder())
        # A section with no heading of its own starts straight after the one before it.
        if len(sections) < len(expected) and expected[len(sections)][0] is None:
            start(page)

    def next_heading() -> str | None:
        return normalise_line(expected[len(sections)][0]) if len(sections) < len(expected) else None

    if expected and expected[0][0] is None:
        start(lines[0][0] if lines else None)
    for page, raw in lines:
        line = normalise_line(raw)
        if not line:
            (texts[-1] if texts else before_first).end_paragraph()
            continue
        if line in skip_lines:
            continue
        if not spec.whole_text and line not in text_lines:
            key = strip_heading_number(line) if spec.numbered and not pending else line
            wanted = next_heading()
            so_far = " ".join([*pending, key])
            looks_like_heading = is_uppercase_heading(key) or (spec.numbered and key != line)
            if wanted and so_far == wanted:
                start(pending_page if pending else page)
                pending = []
                continue
            if wanted and wanted.startswith(so_far + " ") and (pending or looks_like_heading):
                if not pending:
                    pending_page = page
                pending.append(key)
                continue
            if pending:
                raise TemplateError(f"{label}: the heading {' '.join(pending)!r} is incomplete")
            if is_uppercase_heading(key):
                expected_text = f"expected {wanted!r}" if wanted else "expected no more headings"
                raise TemplateError(f"{label}: found the heading {key!r} ({expected_text})")
        if not texts:
            if not spec.text_first:
                raise TemplateError(f"{label}: text before the first heading: {line[:60]!r}")
            before_first.add(line)
            continue
        texts[-1].add(line)
    if pending:
        raise TemplateError(f"{label}: the heading {' '.join(pending)!r} is incomplete")
    if len(sections) < len(expected):
        raise TemplateError(f"{label}: no heading {next_heading()!r}")
    for section, text in zip(sections, texts, strict=True):
        section.guidance = str(text)
    if spec.text_first and str(before_first):
        first = sections[0]
        first.guidance = "\n".join(p for p in (str(before_first), first.guidance) if p)
    return sections


def read_template(spec: TemplateSpec, guidance: GuidanceSpec, lines: Lines) -> Template:
    """One template, read from the lines of the file it's in. Raises TemplateError if they
    don't match the spec."""
    lines = [
        (page, line) for page, line in lines if not normalise_line(line).isdigit()
    ]  # page numbers
    starts = [
        i for i, (_, line) in enumerate(lines) if normalise_line(line) == normalise_line(spec.start)
    ]
    if len(starts) != 1:
        found = "twice" if starts else "nowhere"
        raise TemplateError(f"{spec.title}: {spec.start!r} is {found} in {spec.source}")
    begin = starts[0] + 1
    boundary = re.compile(guidance.boundary)
    end = next(
        (i for i in range(begin, len(lines)) if boundary.match(normalise_line(lines[i][1]))),
        len(lines),
    )
    part = lines[begin:end]
    if spec.title_line:
        while part and not normalise_line(part[0][1]):
            part = part[1:]
        if not part or normalise_line(part[0][1]) != normalise_line(spec.title_line):
            found = normalise_line(part[0][1]) if part else "nothing"
            raise TemplateError(f"{spec.start} should be titled {spec.title_line!r}, not {found!r}")
        part = part[1:]
    if spec.stop_at:
        ends = [
            i for i, (_, line) in enumerate(part) if normalise_line(line).startswith(spec.stop_at)
        ]
        if not ends:
            raise TemplateError(f"{spec.title}: no {spec.stop_at!r}")
        part = part[: ends[0]]
    pages = [page for page, line in [lines[starts[0]], *part] if normalise_line(line) and page]
    return Template(
        key=spec.key,
        title=spec.title,
        kind=spec.kind,
        source_url=guidance.sources[spec.source],
        sha256="",
        first_page=min(pages, default=None),
        last_page=max(pages, default=None),
        sections=split_sections(spec, part),
    )


def pdf_lines(pdf: bytes) -> Lines:
    """A PDF's text, line by line, each with its page number (page 1 first)."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf))
    return [
        (number, line)
        for number, page in enumerate(reader.pages, start=1)
        for line in (page.extract_text() or "").splitlines()
    ]


WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def docx_lines(docx: bytes) -> Lines:
    """A Word file's text, one paragraph per line (table cells included, in reading order),
    each followed by a blank line; list items start with a bullet. No page numbers."""
    with zipfile.ZipFile(io.BytesIO(docx)) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    lines: Lines = []
    for paragraph in root.iter(f"{WORD}p"):
        parts = []
        for node in paragraph.iter():
            if node.tag == f"{WORD}t" and node.text:
                parts.append(node.text)
            elif node.tag in (f"{WORD}tab", f"{WORD}br"):
                parts.append(" ")
        text = "".join(parts)
        if not text.strip():
            continue
        style = paragraph.find(f"{WORD}pPr/{WORD}pStyle")
        listed = paragraph.find(f"{WORD}pPr/{WORD}numPr") is not None or (
            style is not None and style.get(f"{WORD}val") == "ListParagraph"
        )
        lines += [(None, f"{BULLETS[1]} {text}" if listed else text), (None, "")]
    return lines


def read_lines(url: str, content: bytes) -> Lines:
    return docx_lines(content) if url.lower().endswith(".docx") else pdf_lines(content)


def read_guidance(spec: GuidanceSpec, files: dict[str, bytes]) -> Guidance:
    """Every template of one guidance, from its files ({source name: the file's bytes})."""
    lines = {name: read_lines(spec.sources[name], files[name]) for name in spec.sources}
    shas = {name: hashlib.sha256(files[name]).hexdigest() for name in spec.sources}
    templates = []
    for template_spec in spec.templates:
        template = read_template(template_spec, spec, lines[template_spec.source])
        template.sha256 = shas[template_spec.source]
        templates.append(template)
    n_pages = max((page or 0 for page, _ in lines["guidance"]), default=0)
    return Guidance(spec.key, fingerprint(shas), n_pages, templates)


def fingerprint(shas: dict[str, str]) -> str:
    """One sha256 for several files: of their names and sha256s, in name order. With one
    file it's just that file's sha256."""
    if len(shas) == 1:
        return next(iter(shas.values()))
    joined = "\n".join(f"{name} {sha}" for name, sha in sorted(shas.items()))
    return hashlib.sha256(joined.encode()).hexdigest()


def download_guidance_file(url: str) -> bytes:
    import httpx

    response = httpx.get(url, follow_redirects=True, timeout=60)
    response.raise_for_status()
    return response.content


def store_guidance(conn, spec: GuidanceSpec, guidance: Guidance) -> None:
    """Replace one guidance edition and its templates in a single transaction."""
    with conn.transaction():
        conn.execute("DELETE FROM corpus.guidance_documents WHERE guidance = %s", (spec.key,))
        conn.execute(
            "INSERT INTO corpus.guidance_documents (guidance, title, source_url, sha256, n_pages)"
            " VALUES (%s, %s, %s, %s, %s)",
            (spec.key, spec.title, spec.sources["guidance"], guidance.sha256, guidance.n_pages),
        )
        # "appendix" is the template's number in its guidance: Appendix One to Five (2016),
        # Annex A to D as 1 to 4 (2026).
        for number, t in enumerate(guidance.templates, start=1):
            conn.execute(
                "INSERT INTO corpus.templates (template, guidance, appendix, title, kind,"
                " first_page, last_page, source_url, sha256)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    t.key,
                    spec.key,
                    number,
                    t.title,
                    t.kind,
                    t.first_page,
                    t.last_page,
                    t.source_url,
                    t.sha256,
                ),
            )
            for s in t.sections:
                conn.execute(
                    "INSERT INTO corpus.template_sections (template, position, heading, printed,"
                    " guidance, page) VALUES (%s, %s, %s, %s, %s, %s)",
                    (t.key, s.position, s.heading, s.printed, s.guidance, s.page),
                )


def stored_sha256(conn, key: str) -> str | None:
    row = conn.execute(
        "SELECT sha256 FROM corpus.guidance_documents WHERE guidance = %s", (key,)
    ).fetchone()
    return row[0] if row else None


def ensure_templates(conn, key: str = GUIDANCE, log_progress=print) -> str:
    """Makes sure one guidance's templates are stored and up to date: downloads its files
    and stores the templates if they aren't stored yet or a file has changed. Returns the
    files' sha256 (see fingerprint). If a download fails but the templates are stored,
    carries on with those."""
    if key not in GUIDANCES:
        raise ValueError(f"Unknown guidance {key!r}: choose one of {', '.join(GUIDANCES)}")
    spec = GUIDANCES[key]
    stored = stored_sha256(conn, key)
    try:
        files = {name: download_guidance_file(url) for name, url in spec.sources.items()}
    except Exception as error:  # fall back to the templates stored, if any
        if stored:
            log_progress(
                f"NOTE  Couldn't download the {key} guidance ({error}); using the templates stored"
            )
            return stored
        raise
    sha256 = fingerprint({name: hashlib.sha256(f).hexdigest() for name, f in files.items()})
    if sha256 == stored:
        return stored
    guidance = read_guidance(spec, files)
    if stored:
        log_progress(
            f"NOTE  The {key} guidance files have changed since its templates were stored:"
            " storing them again, which removes every dataset's candidates for it"
        )
    store_guidance(conn, spec, guidance)
    n_sections = sum(len(t.sections) for t in guidance.templates)
    log_progress(
        f"OK    Stored {len(guidance.templates)} templates ({n_sections} sections) from the"
        f" {key} guidance (sha256 {sha256[:12]})"
    )
    return sha256
