"""O1 stage 5, shared: the prompts that map a review onto a template, and reading answers.

Two questions, both answered with one letter (llm_batch.py in narratrace-slurm reads it from the
model's first-token probabilities, so every option's probability is kept):

1. Which template is the whole review written to? document_prompt() shows the review's
   title, its opening text and its headings, and lists the guidance's document templates
   (overview report, executive summary, individual management review...; not the action
   plan, which goes with a report rather than being one). Z: none of them.
2. Which section of that template is each review section? section_prompt() lists every
   section of the review's template, then the action plan's (published reports usually end
   with one), then Z: none of these. A section matching none is kept and flagged as
   unmatched. So a review is mapped onto one template, not piece by piece across several.
   Several report sections can share one template section (a timeline and a timeline
   analysis both being the template's timeline, say); the answers are only labels, and the
   sections' text is never changed (the corpus.current_standardised view groups them).
3. For a section that fits none of its template's sections and sits in no matched section,
   or that the model matched with less than 50%: extra_prompt() asks whether it is one of a
   few extra sections that many DHRs have but the templates don't name (glossary, front
   matter...; refine.py). Z: still none.

Every option is in the guidance's own order (appendix, then position), never in score order,
so the letters don't hint at an answer. The options come before the section in a section
prompt: every section of one template then starts with the same words, which vLLM can reuse
from one prompt to the next instead of reading them again.

Change PROMPT_VERSION whenever the wording or the limits change: it is stored with every
answer, so answers from different prompts are never mixed up.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

PROMPT_VERSION = "choose-template-v2"
EXTRA_PROMPT_VERSION = "extra-sections-v2"  # extra_prompt(): change it when that wording changes
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXY"  # one per option (a template has at most 21 sections)
NONE = "Z"
ACTION_PLAN = "action plan"  # the template kind that goes with a report

# How much of each part goes into a prompt (characters, cut at a word).
TEXT_CHARS = 1500  # the start of a section's own text
SUBSECTION_CHARS = 500  # a section's subsections' headings
HEADING_CHARS = 200
PATH_CHARS = 300  # the headings of the sections it sits in
GUIDANCE_CHARS = 200  # what the template says goes in each of its sections
OPENING_CHARS = 800  # a review's text before its first heading (title page, contents)
OUTLINE_CHARS = 3000  # a review's headings
TEMPLATE_OUTLINE_CHARS = 400  # a template's section headings, in a document prompt

SYSTEM = (
    "You map UK Domestic Homicide Review (DHR) reports onto the report templates in statutory "
    "guidance. You answer with a single capital letter."
)


@dataclass
class TemplateSection:
    template: str  # e.g. 'dhr-2016/overview-report'
    position: int
    heading: str
    guidance: str  # what the template says goes there ('' if nothing)


@dataclass
class Template:
    template: str  # e.g. 'dhr-2016/overview-report'
    appendix: int
    title: str  # e.g. 'Overview report template'
    kind: str  # e.g. 'overview report', 'action plan'
    sections: list[TemplateSection] = field(default_factory=list)


@dataclass
class Document:
    doc_id: str
    opening: str  # its text before the first heading
    headings: list[tuple[int, str]]  # (level, heading) in reading order


@dataclass
class Section:
    doc_id: str
    section_id: str
    heading: str
    path: list[str]  # headings of the sections it sits in, outermost first
    text: str  # the start of its own text (not its subsections')
    subsections: str  # its subsections' headings, "; " between them


def shorten(text: str, limit: int) -> str:
    """Text on one line, cut at a word to at most `limit` characters, with … if cut."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    if " " in cut[limit // 2 :]:
        cut = cut[: cut.rindex(" ")]
    return cut.rstrip(" ,;:.") + "…"


def prompt_id(*parts: str) -> str:
    return json.dumps(list(parts), ensure_ascii=False)


def document_templates(templates: list[Template]) -> list[Template]:
    """The templates a whole review can be written to, in the guidance's order."""
    return sorted((t for t in templates if t.kind != ACTION_PLAN), key=lambda t: t.appendix)


def action_plans(templates: list[Template]) -> list[Template]:
    return sorted((t for t in templates if t.kind == ACTION_PLAN), key=lambda t: t.appendix)


def _messages(user: str) -> list[dict]:
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def _outline(document: Document) -> str:
    lines, used = [], 0
    for n, (level, heading) in enumerate(document.headings):
        line = "  " * (max(level, 1) - 1) + shorten(heading, HEADING_CHARS)
        if used + len(line) > OUTLINE_CHARS:
            lines.append(f"… and {len(document.headings) - n} more headings")
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines) or "(no headings)"


def _document_hints(options: list[Template]) -> str:
    """What tells the guidance's kinds of document apart, for the kinds it has templates for."""
    kinds = {t.kind for t in options}
    hints = []
    if "executive summary" in kinds:
        hints.append(
            "A DHR's overview report (the full report) and its executive summary are separate "
            "documents with different templates."
        )
    if "individual management review" in kinds:
        hints.append(
            "An individual management review (or report) is one agency's own report to the "
            "review panel, not the review itself."
        )
    return " ".join(hints) + (" " if hints else "")


def document_prompt(document: Document, templates: list[Template], guidance_title: str) -> dict:
    """Which template the whole review is written to: one letter per document template."""
    options = document_templates(templates)
    if not options or len(options) > len(LETTERS):
        raise ValueError(f"{len(options)} document templates can't be lettered")
    letters = LETTERS[: len(options)]
    lines = []
    for letter, t in zip(letters, options, strict=True):
        outline = shorten("; ".join(s.heading for s in t.sections), TEMPLATE_OUTLINE_CHARS)
        lines.append(f"{letter}. {t.title} (its sections: {outline})")
    lines.append(f"{NONE}. None of these")
    user = "\n".join(
        [
            f'Below are the report templates in "{guidance_title}", and the start of a published '
            "DHR document: its title, its opening text and its headings. Which template is this "
            "document written to?",
            "",
            _document_hints(options)
            + "Judge by the document's structure and contents, not only its title. If it "
            f"follows none of them, choose {NONE}.",
            "",
            "TEMPLATES",
            *lines,
            "",
            "DOCUMENT",
            f"Title: {document.doc_id}",
            "Opening text: "
            + (shorten(document.opening, OPENING_CHARS) or "(none before the first heading)"),
            "Headings:",
            _outline(document),
            "",
            "Answer with the letter only.",
        ]
    )
    return {
        "id": prompt_id(document.doc_id),
        "messages": _messages(user),
        "labels": [*letters, NONE],
        "meta": {
            "doc_id": document.doc_id,
            "options": {letter: t.template for letter, t in zip(letters, options)},
        },
    }


def _report_section(section: Section) -> list[str]:
    """A review section as a prompt shows it: where it sits, its heading, its subsections'
    headings and the start of its own text."""
    report = [f"Report: {section.doc_id}"]
    if section.path:
        report.append("Inside: " + shorten(" > ".join(section.path), PATH_CHARS))
    report.append("Heading: " + (shorten(section.heading, HEADING_CHARS) or "(none)"))
    if section.subsections:
        report.append("Its subsections: " + shorten(section.subsections, SUBSECTION_CHARS))
    text = shorten(section.text, TEXT_CHARS)
    report.append("Start of its text: " + (text or "(no text of its own, only subsections)"))
    return report


def section_options(
    template: Template, plans: list[Template]
) -> list[tuple[Template, TemplateSection]]:
    """A section's options: every section of its review's template, then the action plan's."""
    options = [(template, s) for s in sorted(template.sections, key=lambda s: s.position)]
    if template.kind != ACTION_PLAN:
        for plan in plans:
            options += [(plan, s) for s in sorted(plan.sections, key=lambda s: s.position)]
    return options


def section_prompt(
    section: Section, template: Template, plans: list[Template], guidance_title: str
) -> dict:
    """Which section of its review's template a section is: one letter per template section."""
    options, letters, lines = _option_lines(template, plans)
    report = _report_section(section)
    plan = len({t.template for t, _ in options}) > 1
    user = "\n".join(
        [
            f"Below are the sections of the {template.title}"
            + (" and of the action plan that goes with it" if plan else "")
            + f' in "{guidance_title}", and a section from a published DHR {template.kind} '
            "written to it. Which template section is this report section?",
            "",
            "Judge by what the section is for and what it contains, not only by its heading. "
            "Reports often split one template section over several sections (a chronology and "
            "an analysis of it, say), so choose the template section this one belongs to even "
            f"if others belong to it too. If none of them fits, choose {NONE}.",
            "",
            "TEMPLATE SECTIONS",
            *lines,
            "",
            "REPORT SECTION",
            *report,
            "",
            "Answer with the letter only.",
        ]
    )
    return _section_answer(section, user, letters, options)


def _option_lines(
    template: Template, plans: list[Template]
) -> tuple[list[tuple[Template, TemplateSection]], str, list[str]]:
    """A section question's options, their letters, and the lines that list them."""
    options = section_options(template, plans)
    if not options:
        raise ValueError(f"{template.template} has no sections")
    if len(options) > len(LETTERS):
        raise ValueError(f"{len(options)} options is more than the {len(LETTERS)} letters")
    letters = LETTERS[: len(options)]
    lines, shown = [], None
    for letter, (t, s) in zip(letters, options, strict=True):
        if t.template != shown:
            lines.append(f"{t.title}:")
            shown = t.template
        guidance = shorten(s.guidance, GUIDANCE_CHARS)
        line = f"{letter}. {shorten(s.heading, HEADING_CHARS)}"
        lines.append(f"{line} ({guidance})" if guidance else line)
    lines.append(f"{NONE}. None of these")
    return options, letters, lines


def _section_answer(section: Section, user: str, letters: str, options: list) -> dict:
    return {
        "id": prompt_id(section.doc_id, section.section_id),
        "messages": _messages(user),
        "labels": [*letters, NONE],
        "meta": {
            "doc_id": section.doc_id,
            "section_id": section.section_id,
            "options": {
                letter: [t.template, s.position] for letter, (t, s) in zip(letters, options)
            },
        },
    }


# ---------------------------------------------------------------- prompt v3: the whole review

# Prompt v3 (Rowan, 22 Nov 2026; the workflow's `prompt` input): the section question again,
# but each one first shows the whole review's outline: every section in reading order,
# numbered and indented, with the start of its text. The model then sees what sits next to
# what, which tells apart sections a heading alone can't (an analysis vs lessons learnt,
# background facts vs the chronology). Still one letter per section, so every answer keeps
# its probabilities. Every question about one review starts with the same words (options,
# then outline), so vLLM can reuse them instead of reading them again for each section.
SKIM_PROMPT_VERSION = "choose-template-v3-outline"
SKIM_TEXT_CHARS = (240, 160, 80, 0)  # start of each section's text in the outline: the
# longest that keeps the outline under SKIM_OUTLINE_CHARS
SKIM_OUTLINE_CHARS = 60_000  # about 15,000 tokens; the longest DHRC-RAW outline at 300 is ~70,000
SKIM_MAX_MODEL_LEN = 32_768  # the prompts' token limit a v3 job needs


@dataclass
class Outline:
    doc_id: str
    text: str  # the numbered outline, as the prompt shows it
    numbers: dict[str, int]  # section_id -> its number in the outline
    text_chars: int  # how much of each section's text it shows


def review_outline(sections: list[Section]) -> Outline:
    """One review's sections (in reading order) as a numbered, indented outline, each with the
    start of its own text: as much of it as keeps the outline under SKIM_OUTLINE_CHARS."""
    if not sections or len({s.doc_id for s in sections}) != 1:
        raise ValueError("an outline is of one review's sections")
    numbers = {s.section_id: n for n, s in enumerate(sections, 1)}
    for chars in SKIM_TEXT_CHARS:
        lines, used = [], 0
        for n, s in enumerate(sections, 1):
            line = "  " * len(s.path) + f"[{n}] " + (shorten(s.heading, HEADING_CHARS) or "(none)")
            text = shorten(s.text, chars) if chars else ""
            if text:
                line += f" — {text}"
            if chars == 0 and used + len(line) > SKIM_OUTLINE_CHARS:
                lines.append(f"… and {len(sections) - n + 1} more sections")
                break
            lines.append(line)
            used += len(line) + 1
        if used <= SKIM_OUTLINE_CHARS or chars == 0:
            return Outline(sections[0].doc_id, "\n".join(lines), numbers, chars)
    raise AssertionError("unreachable")


def skim_section_prompt(
    section: Section,
    outline: Outline,
    template: Template,
    plans: list[Template],
    guidance_title: str,
) -> dict:
    """Prompt v3: section_prompt's question, after the whole review's outline."""
    options, letters, lines = _option_lines(template, plans)
    plan = len({t.template for t, _ in options}) > 1
    number = outline.numbers[section.section_id]
    user = "\n".join(
        [
            f"Below are the sections of the {template.title}"
            + (" and of the action plan that goes with it" if plan else "")
            + f' in "{guidance_title}", then the outline of a published DHR {template.kind} '
            "written to it: every section in reading order, numbered, indented under the "
            "section it sits in"
            + (", with the start of its text" if outline.text_chars else "")
            + ". After the outline you are asked which template section one of its sections is.",
            "",
            "Judge by what the section is for and what it contains, not only by its heading, "
            "and use the outline: the sections around it and the order they come in often "
            "show what it is for. Reports often split one template section over several "
            "sections (a chronology and an analysis of it, say), so choose the template section "
            "this one belongs to even if others belong to it too. If none of them fits, "
            f"choose {NONE}.",
            "",
            "TEMPLATE SECTIONS",
            *lines,
            "",
            f"OUTLINE OF THE REPORT: {section.doc_id}",
            outline.text,
            "",
            f"QUESTION: which template section is section [{number}]?",
            *_report_section(section),
            "",
            "Answer with the letter only.",
        ]
    )
    return _section_answer(section, user, letters, options)


def extra_prompt(
    section: Section, template: Template, extras: Template, guidance_title: str
) -> dict:
    """The third question, for a section the model said is none of its template's sections (and
    that sits in no matched section) or matched with less than 50%: is it one of the extra
    sections (refine.py), parts many DHRs have that the templates don't name, or none? One
    letter per extra section offered (refine.asked_extras: not those the rules give)."""
    options = sorted(extras.sections, key=lambda s: s.position)
    if not options or len(options) > len(LETTERS):
        raise ValueError(f"{extras.template} needs 1 to {len(LETTERS)} sections")
    letters = LETTERS[: len(options)]
    lines = []
    for letter, s in zip(letters, options, strict=True):
        guidance = shorten(s.guidance, GUIDANCE_CHARS)
        lines.append(f"{letter}. {s.heading}" + (f" ({guidance})" if guidance else ""))
    lines.append(f"{NONE}. None of these")
    user = "\n".join(
        [
            f"The report section below, from a published DHR {template.kind} written to the "
            f'{template.title} in "{guidance_title}", doesn\'t clearly fit any of that '
            "template's sections. Many DHRs have parts the templates don't name. Which of these "
            "is it?",
            "",
            "Judge by what the section is for and what it contains, not only by its heading. "
            f"If none of them fits, choose {NONE}.",
            "",
            "EXTRA SECTIONS",
            *lines,
            "",
            "REPORT SECTION",
            *_report_section(section),
            "",
            "Answer with the letter only.",
        ]
    )
    return {
        "id": prompt_id(section.doc_id, section.section_id),
        "messages": _messages(user),
        "labels": [*letters, NONE],
        "meta": {
            "doc_id": section.doc_id,
            "section_id": section.section_id,
            "options": {
                letter: [extras.template, s.position] for letter, s in zip(letters, options)
            },
        },
    }


def _by_option(probabilities: dict, options: dict, key) -> dict:
    """The model's probabilities by what each letter stands for ("none" for Z)."""
    by_option = {}
    for letter, probability in probabilities.items():
        if letter == NONE:
            by_option["none"] = probability
        elif letter in options:
            by_option[key(options[letter])] = probability
    return by_option


def _answer(result: dict, options: dict):
    """(answer, error): the letter, or None with why there is no usable answer."""
    answer, error = result.get("answer"), result.get("error")
    if answer is not None and answer != NONE and answer not in options:
        return None, f"answered {answer!r}, which isn't one of the options"
    return answer, error


def interpret_document(result: dict) -> dict:
    """One document answer as a row for corpus.document_choices: the template the review is
    written to (None for Z, "none of these", or when there is no answer)."""
    meta = result.get("meta") or {}
    options = meta.get("options") or {}
    probabilities = result.get("probabilities") or {}
    answer, error = _answer(result, options)
    return {
        "doc_id": meta.get("doc_id"),
        "template": options[answer] if answer not in (None, NONE) else None,
        "answer": answer,
        "probability": probabilities.get(answer) if answer is not None else None,
        "probabilities": _by_option(probabilities, options, lambda template: template),
        "error": error,
    }


def interpret(result: dict) -> dict:
    """One section answer as a row for corpus.template_choices: the template section its
    letter stands for (None for Z, "none of these", or when there is no answer)."""
    meta = result.get("meta") or {}
    options = meta.get("options") or {}
    probabilities = result.get("probabilities") or {}
    answer, error = _answer(result, options)
    template = position = None
    if answer not in (None, NONE):
        template, position = options[answer][0], int(options[answer][1])
    return {
        "doc_id": meta.get("doc_id"),
        "section_id": meta.get("section_id"),
        "template": template,
        "template_position": position,
        "answer": answer,
        "probability": probabilities.get(answer) if answer is not None else None,
        "none_probability": probabilities.get(NONE, 0.0),
        "probabilities": _by_option(probabilities, options, lambda o: f"{o[0]}#{o[1]}"),
        "n_options": len(options),
        "error": error,
    }
