"""Tests for f/dhr/O1/find_candidate_template_sections/templates.py and f/dhr/O1/find_candidate_template_sections/find_candidate_template_sections.py.

The guidance files are made up here, laid out the way the real ones read: the PDFs as
pypdf reads them (2016: headings in capitals, page numbers on their own line, bullet points
as a private-use character; 2026: numbered headings, bullet characters) and the 2026 DARDR
template as a Word file (a table: numbers in one column, headings and text in the other),
with invented text under each heading. Reading the real files is checked in Windmill,
where they can be downloaded.
"""

import hashlib
import io
import zipfile

import numpy as np
import pytest

from f.dhr.O1.find_candidate_template_sections.find_candidate_template_sections import (
    Section,
    heading_scores,
    heading_words,
    rank_scores,
    select_candidates,
)
from f.dhr.O1.find_candidate_template_sections.templates import (
    BULLETS,
    DARDR_2026,
    DHR_2016,
    GUIDANCE,
    GUIDANCES,
    TemplateError,
    docx_lines,
    fingerprint,
    heading_name,
    read_guidance,
    read_template,
)

BULLET_2016, BULLET_2026 = BULLETS


def section_lines(spec, numbered=False):
    """Made-up lines for one template's sections: each heading (long ones over two lines,
    numbered if asked) with an invented line of text under it."""
    lines = []
    for number, (printed, name) in enumerate(spec.sections):
        if printed:
            words = printed.split()
            if len(words) > 8:
                lines += [
                    " ".join(words[: len(words) // 2]) + " ",
                    " ".join(words[len(words) // 2 :]),
                ]
            else:
                lines.append(printed + "  ")
            if numbered and number:
                lines[-1 if len(words) <= 8 else -2] = (
                    f"{number} " + lines[-1 if len(words) <= 8 else -2]
                )
        label = (name or printed).lower()
        lines += [f"Invented text about {label}, ", "carried on over a second line. ", " "]
    return lines


def pdf_2016(changes=None):
    """A made-up 2016 guidance as (page, line): 30 pages of other text (with a contents
    page naming the appendices), then one page per appendix."""
    changes = changes or {}
    lines = [(1, "APPENDIX THREE - Overview Report Template ........ 35")]
    lines += [(n, f"Part of the guidance on page {n}.") for n in range(2, 31)]
    for spec in DHR_2016.templates:
        page = len({p for p, _ in lines}) + 1
        body = [f" {page} ", f"{spec.start} ", " ", f"{spec.title_line} ", " "]
        if spec.whole_text:
            body += ["RECOMMENDATION ", "Some invented table text. "]
        else:
            body += section_lines(spec)
        if spec.key.endswith("/imr"):
            body += ["WHAT WAS YOUR AGENCY’S INVOLVEMENT WITH THE VICTIM? "]
        if spec.stop_at:
            body += [" ", f"{spec.stop_at} – FOR HOME OFFICE DATA COLLECTION ONLY ",
                     "DOMESTIC HOMICIDE REVIEW ", "Community Safety Partnership  "]  # fmt: skip
        body = changes.get(spec.key, lambda b: b)(body)
        lines += [(page, line) for line in body]
    return lines


def read_2016(key, changes=None):
    spec = next(t for t in DHR_2016.templates if t.key == key)
    return read_template(spec, DHR_2016, pdf_2016(changes))


def pdf_2026(changes=None):
    """A made-up 2026 guidance PDF as (page, line): Annexes B to E, one page each."""
    changes = changes or {}
    lines = [(1, "Annex B: Action Plan Template and Guidance ........ 57")]
    pages = {
        "dardr-2026/action-plan": ["RECOMMENDATION ", "LEVEL OF ", "1.1 Action ", " ",
                                   "Action Plan Guidance ", f"{BULLET_2026} Actions must be SMART. "],
        "dardr-2026/scoping-review": section_lines(DARDR_2026.templates[2], numbered=True),
        "dardr-2026/imr": ["Handling note: to be shared only with those who need it ", " ",
                           "Name of person subject to review  ", " ", *section_lines(DARDR_2026.templates[3])],
    }  # fmt: skip
    for spec in DARDR_2026.templates[1:]:
        page = len({p for p, _ in lines}) + 1
        body = [f" {page} ", f"{spec.start} ", " ", *pages[spec.key]]
        body = changes.get(spec.key, lambda b: b)(body)
        lines += [(page, line) for line in body]
    end = len({p for p, _ in lines}) + 1
    lines += [
        (end, "Annex E: Quality Assurance Board DARDR Feedback Template "),
        (end, "TITLE OF DARDR "),
    ]
    return lines


def read_2026(key, changes=None):
    spec = next(t for t in DARDR_2026.templates if t.key == key)
    return read_template(spec, DARDR_2026, pdf_2026(changes))


WORD_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def paragraph(text, listed=False):
    style = '<w:pPr><w:pStyle w:val="ListParagraph"/></w:pPr>' if listed else ""
    return f'<w:p>{style}<w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>'


def word_file(rows, before="Annex A: DARDR Template"):
    """A made-up Word file: a paragraph, then a table of rows (number, [paragraphs])."""
    table_rows = []
    for number, texts in rows:
        paragraphs = "".join(paragraph(text, is_listed) for text, is_listed in texts)
        table_rows.append(f"<w:tr><w:tc>{paragraph(number)}</w:tc><w:tc>{paragraphs}</w:tc></w:tr>")
    cells = "".join(table_rows)
    xml = f"<w:document {WORD_NS}><w:body>{paragraph(before)}<w:tbl>{cells}</w:tbl></w:body></w:document>"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", xml)
    return buffer.getvalue()


def dardr_word_file(changes=None):
    """The 2026 DARDR template as a made-up Word file, laid out like the real one."""
    rows = []
    for number, (printed, _) in enumerate(DARDR_2026.templates[0].sections):
        texts = [(printed, False), (f"Invented text about {printed.lower()}.", False),
                 ("A point in a list", True)]  # fmt: skip
        rows.append((str(number) if number else "", texts))
        if printed == "LESSONS LEARNT AND RECOMMENDATIONS":
            rows.append(("", [("ANNEX A: DARDR PROCESS INFORMATION", False)]))
    return word_file((changes or (lambda r: r))(rows))


# ----------------------------------------------------------------- the 2016 guidance


def test_every_2016_template_is_read_with_its_sections_in_order():
    read = [read_2016(spec.key) for spec in DHR_2016.templates]
    assert [len(t.sections) for t in read] == [len(s.sections) for s in DHR_2016.templates]
    overview = read[2]
    assert (overview.key, overview.title, overview.kind) == (
        "dhr-2016/overview-report", "Overview report template", "overview report"
    )  # fmt: skip
    assert overview.first_page == overview.last_page == 33
    assert overview.source_url == DHR_2016.sources["guidance"]
    headings = [s.heading for s in overview.sections]
    assert headings[:4] == ["Title page of overview report", "List of contents page",
                            "Introduction", "Timescales"]  # fmt: skip
    assert headings[-1] == "Recommendations"
    assert [s.position for s in overview.sections] == list(range(1, 22))


def test_text_goes_with_its_heading_and_lines_are_joined():
    timescales = read_2016("dhr-2016/overview-report").sections[3]
    assert timescales.printed and timescales.page == 33
    assert timescales.guidance == "Invented text about timescales, carried on over a second line."


def test_a_heading_printed_over_two_lines_is_one_heading():
    involvement = read_2016("dhr-2016/overview-report").sections[7]
    assert involvement.heading == (
        "Involvement of family, friends, work colleagues, neighbours and wider community"
    )
    assert involvement.guidance.startswith("Invented text about involvement of family")


def test_a_section_with_no_heading_starts_straight_after_the_one_before():
    # In the real PDF the overview report's introduction is the text after "LIST OF
    # CONTENTS PAGE", with no heading of its own: that text goes to the introduction.
    overview = read_2016("dhr-2016/overview-report")
    contents, introduction = overview.sections[1], overview.sections[2]
    assert contents.printed and not introduction.printed
    assert contents.guidance == ""
    assert introduction.guidance == (
        "Invented text about list of contents page, carried on over a second line.\n"
        "Invented text about introduction, carried on over a second line."
    )


def test_an_action_plan_is_one_section_of_all_its_text_capitals_included():
    plan = read_2016("dhr-2016/action-plan")
    assert [(s.heading, s.printed) for s in plan.sections] == [("Action plan", False)]
    assert plan.sections[0].guidance == "RECOMMENDATION Some invented table text."


def test_capital_lines_that_are_text_stay_text():
    imr = read_2016("dhr-2016/imr")
    assert imr.sections[-1].guidance.endswith("WHAT WAS YOUR AGENCY'S INVOLVEMENT WITH THE VICTIM?")


def test_the_data_collection_form_is_not_part_of_the_executive_summary():
    summary = read_2016("dhr-2016/executive-summary")
    assert summary.sections[-1].heading == "Recommendations from the review"
    assert "COLLECTION" not in summary.sections[-1].guidance
    assert "Community Safety Partnership" not in summary.sections[-1].guidance


def test_bullet_points_each_start_a_line():
    def bullets(body):
        at = body.index("Invented text about timescales, ")
        return [*body[:at], "Some text: ", f"{BULLET_2016} first point ",
                f"{BULLET_2016} second point, ", "which wraps ", " ", *body[at + 1 :]]  # fmt: skip

    timescales = read_2016("dhr-2016/overview-report", {"dhr-2016/overview-report": bullets})
    assert timescales.sections[3].guidance == (
        "Some text:\n- first point\n- second point, which wraps\ncarried on over a second line."
    )


def test_an_unlisted_heading_stops_the_reading():
    def glossary(body):
        return ["GLOSSARY" if line.startswith("TIMESCALES") else line for line in body]

    with pytest.raises(TemplateError, match="GLOSSARY"):
        read_2016("dhr-2016/overview-report", {"dhr-2016/overview-report": glossary})


def test_a_missing_heading_stops_the_reading():
    def without(body):
        return [line for line in body if not line.startswith("DISSEMINATION")]

    with pytest.raises(TemplateError, match="DISSEMINATION"):
        read_2016("dhr-2016/overview-report", {"dhr-2016/overview-report": without})


def test_a_wrong_title_or_a_missing_start_stops_the_reading():
    def retitled(body):
        return ["SOMETHING ELSE" if "SUMMARY TEMPLATE" in line else line for line in body]

    with pytest.raises(TemplateError, match="should be titled"):
        read_2016("dhr-2016/executive-summary", {"dhr-2016/executive-summary": retitled})
    with pytest.raises(TemplateError, match="nowhere"):
        read_template(DHR_2016.templates[4], DHR_2016, [x for x in pdf_2016() if x[0] != 35])


def test_a_line_in_the_contents_naming_an_appendix_doesnt_start_it():
    overview = read_2016("dhr-2016/overview-report")
    assert overview.first_page == 33  # not page 1, where the contents name it


# ----------------------------------------------------------------- the 2026 guidance


def test_the_2026_dardr_template_is_read_from_its_word_file():
    template = read_template(DARDR_2026.templates[0], DARDR_2026, docx_lines(dardr_word_file()))
    assert template.source_url == DARDR_2026.sources["annex-a"]
    assert (template.first_page, template.last_page) == (None, None)  # a Word file has no pages
    assert [s.heading for s in template.sections][:4] == [
        "Title page of DARDR", "List of contents page", "Pen portrait of victim",
        "DARDR Chair and CSP condolences",
    ]  # fmt: skip
    assert [s.heading for s in template.sections][-3:] == [
        "DARDR author", "Parallel reviews", "Scoping review"
    ]  # fmt: skip
    assert all(s.page is None for s in template.sections)
    timescales = next(s for s in template.sections if s.heading == "Timescales")
    assert timescales.guidance == "Invented text about timescales.\n- A point in a list"


def test_the_word_file_divider_is_neither_heading_nor_text():
    template = read_template(DARDR_2026.templates[0], DARDR_2026, docx_lines(dardr_word_file()))
    assert all("PROCESS INFORMATION" not in s.guidance for s in template.sections)


def test_a_heading_missing_from_the_word_file_stops_the_reading():
    def without(rows):
        return [r for r in rows if r[1][0][0] != "DARDR AUTHOR"]

    with pytest.raises(TemplateError, match="DARDR AUTHOR"):
        read_template(DARDR_2026.templates[0], DARDR_2026, docx_lines(dardr_word_file(without)))


def test_word_paragraphs_come_one_per_line_with_list_items_bulleted():
    lines = docx_lines(word_file([("1", [("HEADING", False), ("An item", True)])], before="Intro"))
    assert lines == [(None, "Intro"), (None, ""), (None, "1"), (None, ""), (None, "HEADING"),
                     (None, ""), (None, f"{BULLET_2026} An item"), (None, "")]  # fmt: skip


def test_numbered_questions_are_headings_even_over_two_lines():
    scoping = read_2026("dardr-2026/scoping-review")
    assert [s.heading for s in scoping.sections][:3] == [
        "Title page of scoping review",
        "How was the CSP notified of this death and on what date did this take place?",
        "Summary of fatal incident",
    ]
    assert scoping.sections[6].heading.startswith("If the Scoping Review Panel has agreed")
    assert scoping.sections[6].guidance.startswith("Invented text about if the scoping review")


def test_dashes_and_curly_apostrophes_in_headings_are_evened_out():
    def curly(body):
        return [line.replace(" - ", " – ").replace("friends'", "friends’") for line in body]

    scoping = read_2026("dardr-2026/scoping-review", {"dardr-2026/scoping-review": curly})
    assert scoping.sections[3].heading == (
        "Background - what has been learnt about the victim and perpetrator?"
    )
    assert "friends' views" in scoping.sections[7].heading


def test_the_2026_action_plan_keeps_its_capital_table_headings_as_text():
    plan = read_2026("dardr-2026/action-plan")
    assert [(s.heading, s.printed) for s in plan.sections] == [("Action plan", False)]
    assert plan.sections[0].guidance.startswith("RECOMMENDATION LEVEL OF 1.1 Action")
    assert plan.sections[0].guidance.endswith("- Actions must be SMART.")


def test_the_2026_imr_front_sheet_goes_with_its_first_section():
    imr = read_2026("dardr-2026/imr")
    assert [s.heading for s in imr.sections][
        -1
    ] == "Effective practice, lessons learnt & recommendations"
    assert imr.sections[0].heading == "Introduction"
    assert imr.sections[0].guidance.startswith("Handling note: to be shared only")
    assert "TITLE OF DARDR" not in imr.sections[-1].guidance  # Annex E ends Annex D


def test_a_whole_guidance_is_read_from_its_files_with_their_checksums(monkeypatch):
    monkeypatch.setattr(
        "f.dhr.O1.find_candidate_template_sections.templates.pdf_lines", lambda pdf: pdf_2026()
    )
    files = {"guidance": b"made-up 2026 pdf", "annex-a": dardr_word_file()}
    guidance = read_guidance(DARDR_2026, files)
    assert [t.key for t in guidance.templates] == [t.key for t in DARDR_2026.templates]
    shas = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    assert guidance.sha256 == fingerprint(shas) != shas["guidance"]
    assert guidance.templates[0].sha256 == shas["annex-a"]
    assert guidance.templates[1].sha256 == shas["guidance"]
    assert guidance.n_pages == 5  # contents, then Annexes B to E


def test_one_file_s_fingerprint_is_its_own_checksum():
    assert fingerprint({"guidance": "abc"}) == "abc"


def test_heading_names_keep_acronyms_and_sentence_case_questions():
    assert heading_name("THE DARDR PANEL MEMBERS") == "The DARDR panel members"
    assert heading_name("DARDR AUTHOR") == "DARDR author"
    assert heading_name("Summary of fatal incident") == "Summary of fatal incident"


def test_both_guidances_are_known_with_2016_the_default():
    assert set(GUIDANCES) == {"dhr-2016", "dardr-2026"} and GUIDANCE == "dhr-2016"
    assert all(t.key.startswith(f"{key}/") for key, g in GUIDANCES.items() for t in g.templates)
    assert all(t.source in g.sources for g in GUIDANCES.values() for t in g.templates)
    assert all(url.startswith("https://assets.publishing.service.gov.uk/")
               for g in GUIDANCES.values() for url in g.sources.values())  # fmt: skip


# ------------------------------------------------------------------ comparing headings
# ------------------------------------------------------------------ comparing headings


@pytest.mark.parametrize(
    ("heading", "words"),
    [
        ("3.2 Lessons Learned", "lesson learn"),
        ("LESSONS TO BE LEARNT", "lesson learn"),
        ("Section 4: Analysis", "analysi"),
        ("Two: The Facts", "fact"),
        ("SIX RECOMMENDATIONS", "recommendation"),
        ("iv. Equality and Diversity", "equality diversity"),
        ("A. Terms of reference", "term reference"),
        ("Appendix 1 Action Plan", "action plan"),
        ("Chronology/Overview", "chronology overview"),
        ("Victim’s family & friends", "victim family friend"),
        ("1.1", ""),
        ("Address", "address"),  # a double s isn't a plural ending
        ("One-off contact", "one off contact"),  # a number word that isn't numbering
    ],
)
def test_heading_words(heading, words):
    assert heading_words(heading) == words


def test_an_exact_heading_scores_highest_and_a_number_scores_nothing():
    templates = ["Conclusions", "Lessons to be learnt", "Recommendations"]
    scores = heading_scores(
        ["5. Conclusions", "Conclusions and lessons to be learnt", "1.1"], templates
    )
    assert scores[0, 0] == 100
    assert scores[1, 0] > 60 and scores[1, 1] > 60  # a combined heading is like both
    assert scores[1, 1] < 100
    assert not scores[2].any()


def test_heading_scores_with_no_sections():
    assert heading_scores([], ["Conclusions"]).shape == (0, 1)


def test_ranks_count_from_the_highest_and_keep_template_order_on_ties():
    assert rank_scores(np.array([[0.2, 0.9, 0.2, 0.5]])).tolist() == [[3, 1, 4, 2]]


def test_candidates_are_the_best_by_text_and_the_best_by_heading_if_alike_enough():
    heading = np.array([[100.0, 70.0, 10.0, 0.0], [50.0, 40.0, 30.0, 20.0]])
    text = np.array([[0.1, 0.2, 0.9, 0.3], [0.4, 0.3, 0.2, 0.8]])
    found = select_candidates(heading, text, k=1, min_heading_score=60)
    assert {(c.section, c.template_section) for c in found} == {(0, 0), (0, 2), (1, 3)}
    first = next(c for c in found if (c.section, c.template_section) == (0, 0))
    assert (first.heading_score, first.heading_rank, first.text_rank) == (100.0, 1, 4)
    assert first.text_score == pytest.approx(0.1)
    # With k = 2 the second-best of each way joins, if alike enough by heading.
    found = select_candidates(heading, text, k=2, min_heading_score=60)
    assert {(c.section, c.template_section) for c in found} == {
        (0, 0), (0, 1), (0, 2), (0, 3), (1, 3), (1, 0)
    }  # fmt: skip


def test_a_section_is_embedded_with_its_heading_and_subsections():
    section = Section("doc", "/page/1/SectionHeader/0", "Introduction", "Some text.", "A; B")
    assert section.for_embedding() == "Introduction\nSome text.\nSubsections: A; B"
    assert Section("d", "s", "Contents", "", "").for_embedding() == "Contents"
