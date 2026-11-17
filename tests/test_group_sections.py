"""Tests for f/dhr/group_sections.py, using small made-up reviews (no real DHR text in the repo).
Storing in the database is checked against a real database by hand."""

import pytest

from f.dhr.group_sections import GroupError, Totals, group, outline, plain_text


def heading(page, n, text, level=None, parent=None, trail=None, tag="h2"):
    """A heading block. level/parent: the checked fields (left out if level is None)."""
    block = {
        "id": f"/page/{page}/SectionHeader/{n}",
        "block_type": "SectionHeader",
        "page": page,
        "html": f"<{tag}>{text}</{tag}>",
        "section_hierarchy": trail or {},
    }
    if level is not None:
        block |= {"section_level": level, "parent_section_id": parent}
    return block


def text(page, n, words, section="unset", trail=None, block_type="Text"):
    """A content block. section: the checked containing_section_id ("unset" leaves it out)."""
    block = {
        "id": f"/page/{page}/{block_type}/{n}",
        "block_type": block_type,
        "page": page,
        "html": f"<p>{words}</p>",
        "section_hierarchy": trail or {},
    }
    if section != "unset":
        block["containing_section_id"] = section
    return block


def review(*pages):
    return {"children": [{"id": f"/page/{n}/Page/{n}", "block_type": "Page", "children": list(p)}
                         for n, p in enumerate(pages)]}  # fmt: skip


H1 = "/page/1/SectionHeader/0"
H2 = "/page/1/SectionHeader/2"
H3 = "/page/2/SectionHeader/1"

# A title page, then "1 Introduction" with a subsection "1.1 Scope" that runs across a page
# break, then "2 Findings". Both the checked fields and Datalab's section_hierarchy are given.
CHECKED = review(
    [text(0, 0, "Title page", section=None)],
    [
        heading(1, 0, "1 Introduction", level=1),
        text(1, 1, "Intro words", section=H1, trail={"1": H1}),
        heading(1, 2, "1.1 Scope", level=2, parent=H1, trail={"1": H1}, tag="h3"),
        text(1, 3, "Scope words", section=H2, trail={"1": H1, "2": H2}),
    ],
    [
        text(2, 0, "More scope words", section=H2, trail={"1": H1, "2": H2}),
        heading(2, 1, "2 Findings", level=1, trail={}),
        text(2, 2, "Findings words", section=H3, trail={"1": H3}),
    ],
)


def test_blocks_go_under_their_sections_in_reading_order():
    grouped = group(CHECKED, "checked")
    assert [(b.block_id, b.position, b.section_id) for b in grouped.blocks] == [
        ("/page/0/Text/0", 0, None),
        (H1, 1, H1),
        ("/page/1/Text/1", 2, H1),
        (H2, 3, H2),
        ("/page/1/Text/3", 4, H2),
        ("/page/2/Text/0", 5, H2),
        (H3, 6, H3),
        ("/page/2/Text/2", 7, H3),
    ]


def test_sections_nest_and_join_across_page_breaks():
    sections = {s.section_id: s for s in group(CHECKED, "checked").sections}
    intro, scope, findings = sections[H1], sections[H2], sections[H3]
    assert (intro.parent_id, intro.level, intro.heading) == (None, 1, "1 Introduction")
    assert (scope.parent_id, scope.level) == (H1, 2)
    # 1.1 Scope starts on page 1 and carries on onto page 2: one section, not two.
    assert (scope.position, scope.last_position, scope.first_page, scope.last_page) == (3, 5, 1, 2)
    # 1 Introduction covers its subsection too, but only one block sits directly in it.
    assert (intro.position, intro.last_position, intro.n_blocks) == (1, 5, 1)
    assert (findings.first_page, findings.last_page, findings.n_blocks) == (2, 2, 1)


def test_datalabs_headings_give_the_same_structure():
    checked, datalab = group(CHECKED, "checked"), group(CHECKED, "datalab")
    assert [b.section_id for b in datalab.blocks] == [b.section_id for b in checked.blocks]
    assert [(s.section_id, s.parent_id, s.level) for s in datalab.sections] == [
        (s.section_id, s.parent_id, s.level) for s in checked.sections
    ]


def test_outline_is_indented_by_level():
    assert outline(group(CHECKED, "checked")) == [
        "1 Introduction  [p1-2, 1]",
        "  1.1 Scope  [p1-2, 2]",
        "2 Findings  [p2, 1]",
    ]


def test_totals_count_sections_across_pages_and_blocks_before_the_first_heading():
    totals = Totals()
    totals.add(group(CHECKED, "checked"))
    assert (totals.n_documents, totals.n_sections, totals.n_blocks) == (1, 3, 8)
    assert (totals.n_across_pages, totals.n_before_first_heading) == (2, 1)


# ----------------------------------------------------------------- what stops a review


REMOVE = object()


def changed(review_json, page, index, **fields):
    """A copy of a review with one block's fields changed (REMOVE takes a field away)."""
    pages = [dict(p, children=[dict(b) for b in p["children"]]) for p in review_json["children"]]
    block = pages[page]["children"][index]
    for name, value in fields.items():
        if value is REMOVE:
            block.pop(name, None)
        else:
            block[name] = value
    return {"children": pages}


def test_block_without_its_checked_section_field_stops_the_review():
    broken = changed(CHECKED, 1, 1, containing_section_id=REMOVE)
    with pytest.raises(GroupError, match="/page/1/Text/1 has no containing_section_id"):
        group(broken, "checked")


def test_block_pointing_to_a_missing_heading_stops_the_review():
    broken = changed(CHECKED, 1, 1, containing_section_id="/page/9/SectionHeader/9")
    with pytest.raises(GroupError, match="which isn't a heading"):
        group(broken, "checked")


def test_heading_must_sit_under_a_higher_level():
    broken = changed(CHECKED, 1, 2, section_level=1)  # 1.1 Scope claims level 1 under level 1
    with pytest.raises(GroupError, match="sits under /page/1/SectionHeader/0, which is level 1"):
        group(broken, "checked")


def test_a_section_interrupted_by_another_stops_the_review():
    # The last block says it belongs to 1.1 Scope, but 2 Findings has started by then.
    broken = changed(CHECKED, 2, 2, containing_section_id=H2)
    with pytest.raises(GroupError, match="is interrupted by /page/2/SectionHeader/1"):
        group(broken, "checked")


def test_block_with_no_section_in_the_middle_of_one_stops_the_review():
    broken = changed(CHECKED, 1, 3, containing_section_id=None)
    with pytest.raises(GroupError, match="interrupted by /page/1/Text/3, which belongs to no"):
        group(broken, "checked")


def test_review_without_pages_stops():
    with pytest.raises(GroupError, match="no pages"):
        group({"metadata": {}}, "checked")


# ----------------------------------------------------------------- a heading printed too low

APPENDICES, APPENDIX_1, TITLE = (
    "/page/0/SectionHeader/0",
    "/page/1/SectionHeader/1",
    "/page/1/SectionHeader/0",
)


def appendix(*page_one):
    """'APPENDICES', then a page that prints an appendix's title above its 'APPENDIX 1'
    label, so the page is read child heading first."""
    return review([heading(0, 0, "APPENDICES", level=1)], list(page_one))


def test_heading_printed_below_its_own_section_moves_to_its_start():
    raw = appendix(
        heading(1, 0, "Guidance title", level=3, parent=APPENDIX_1),
        heading(1, 1, "APPENDIX 1", level=2, parent=APPENDICES),
        text(1, 2, "Guidance words", section=TITLE),
    )
    grouped = group(raw, "checked")
    assert grouped.n_moved_headings == 1
    assert [b.block_id for b in grouped.blocks] == [
        APPENDICES, APPENDIX_1, TITLE, "/page/1/Text/2"
    ]  # fmt: skip
    assert outline(grouped) == [
        "APPENDICES  [p0-1, 0]",
        "  APPENDIX 1  [p1, 0]",
        "    Guidance title  [p1, 1]",
    ]


def test_heading_never_moves_past_a_block_from_another_section():
    raw = appendix(
        heading(1, 0, "Guidance title", level=3, parent=APPENDIX_1),
        text(1, 5, "Words of APPENDICES itself", section=APPENDICES),
        heading(1, 1, "APPENDIX 1", level=2, parent=APPENDICES),
        text(1, 2, "Guidance words", section=TITLE),
    )
    with pytest.raises(GroupError, match="comes before its parent /page/1/SectionHeader/1"):
        group(raw, "checked")


# ----------------------------------------------------------------- Datalab's own output


def test_page_headers_and_footers_are_left_out():
    raw = review(
        [
            text(0, 0, "OFFICIAL", block_type="PageHeader"),
            heading(0, 1, "Overview", tag="h1"),
            text(0, 2, "Words", trail={"1": "/page/0/SectionHeader/1"}),
            text(
                0, 3, "Page 1 of 2", block_type="PageFooter", trail={"1": "/page/0/SectionHeader/1"}
            ),
        ],
        [
            text(1, 0, "OFFICIAL", block_type="PageHeader", trail={"1": "/page/0/SectionHeader/1"}),
            text(1, 1, "More words", trail={"1": "/page/0/SectionHeader/1"}),
        ],
    )
    grouped = group(raw, "datalab")
    assert grouped.n_page_furniture == 3
    assert [b.block_id for b in grouped.blocks] == [
        "/page/0/SectionHeader/1", "/page/0/Text/2", "/page/1/Text/1"
    ]  # fmt: skip
    (section,) = grouped.sections
    assert (section.level, section.first_page, section.last_page) == (1, 0, 1)


def test_last_heading_with_nothing_after_it_gets_its_level_from_its_tag():
    raw = review(
        [
            heading(0, 0, "Report", tag="h1"),
            text(0, 1, "Words", trail={"1": "/page/0/SectionHeader/0"}),
            heading(0, 2, "Appendix", tag="h2", trail={"1": "/page/0/SectionHeader/0"}),
        ]
    )
    appendix = group(raw, "datalab").sections[-1]
    assert (appendix.level, appendix.parent_id) == (2, "/page/0/SectionHeader/0")


# ----------------------------------------------------------------- headings outside the hierarchy


def test_heading_left_out_of_the_hierarchy_becomes_an_ordinary_block():
    # An empty heading the checks left out (no level, nothing points to it) sits in the middle
    # of 1.1 Scope: it must not split the section.
    raw = changed(
        CHECKED, 1, 3, block_type="SectionHeader", html="<h4></h4>", containing_section_id=REMOVE
    )
    grouped = group(raw, "checked")
    assert grouped.n_loose_headings == 1
    assert [s.section_id for s in grouped.sections] == [H1, H2, H3]
    assert grouped.blocks[4].section_id == H2


# ----------------------------------------------------------------- the words in a block


def test_plain_text_puts_paragraphs_and_list_items_on_their_own_lines():
    html = "<ol><li><p>1.1 First.</p><p>Second &amp; third.</p></li><li>1.2  Next\n item</li></ol>"
    assert plain_text(html) == "1.1 First.\nSecond & third.\n1.2 Next item"


def test_plain_text_keeps_table_rows_together():
    html = "<table>\n<tr>\n<td>1</td>\n<td>Introduction</td>\n<td></td>\n</tr>\n</table>"
    assert plain_text(html) == "1 | Introduction"


def test_block_that_is_only_an_image_gives_its_description():
    assert plain_text('<img alt="A signature" src="x.jpg"/>') == "A signature"
    assert plain_text('<img alt="A logo"/><p>Caption words</p>') == "Caption words"


def test_heading_split_over_lines_is_one_line():
    raw = review([heading(0, 0, "PART ONE<br>BACKGROUND", level=1)])
    assert group(raw, "checked").sections[0].heading == "PART ONE BACKGROUND"
