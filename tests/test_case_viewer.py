"""Tests for the case viewer's scripts (the database parts are checked by hand on the server)."""

from f.dhr.O1.link_case_metadata.get_case_details import group_details, split_detail
from f.dhr.O1.link_case_metadata.get_reviews import build_facets, review_facts, year


def test_plain_heading():
    assert split_detail("About the victim / Sex", "Female") == ("About the victim", "Sex", "Female")


def test_sub_heading_goes_into_the_value():
    assert split_detail("About the death / Police force area / East", "Essex") == (
        "About the death",
        "Police force area",
        "East: Essex",
    )


def test_sub_heading_with_slashes_stays_whole():
    group = "About the victim / Ethnicity / Black / African / Caribbean / Black British"
    assert split_detail(group, "Caribbean") == (
        "About the victim",
        "Ethnicity",
        "Black / African / Caribbean / Black British: Caribbean",
    )


def test_themes_are_grouped_by_who_they_are_about():
    assert split_detail("Filter by theme", "Victim - alcohol misuse") == (
        "Filter by theme",
        "Victim",
        "alcohol misuse",
    )


def test_categories_have_no_attribute():
    assert split_detail("Most filtered for DHRs", "Intimate-partner homicide") == (
        "Most filtered for DHRs",
        "",
        "Intimate-partner homicide",
    )


def test_sections_come_in_page_order_with_friendly_names():
    rows = [
        ("Filter by theme", "Victim - housing issues"),
        ("About the victim / Sex", "Female"),
        ("Most filtered for DHRs", "Intimate-partner homicide"),
        ("About the victim / Age (years)", "25 - 34"),
        ("Filter by theme", "Victim - alcohol misuse"),
        ("Somewhere new / Thing", "x"),
    ]
    sections = group_details(rows)
    assert [s["section"] for s in sections] == [
        "Categories",
        "About the victim",
        "Themes",
        "Somewhere new",
    ]
    assert sections[1]["attributes"] == [
        {"attribute": "Age (years)", "values": ["25 - 34"]},
        {"attribute": "Sex", "values": ["Female"]},
    ]
    assert sections[2]["attributes"] == [
        {"attribute": "Victim", "values": ["alcohol misuse", "housing issues"]}
    ]


def test_no_details_gives_no_sections():
    assert group_details([]) == []


# ----------------------------------------------------------------- the case explorer's facets


def explorer_review(doc_id, **facts):
    review = {
        "doc_id": doc_id,
        "source": "Home Office website",
        "in_library": True,
        "loaded": True,
        "csp_region": "South West",
        "csp": "Somerset",
        "date_of_death": "May 2019",
        "uploaded": "November 2023",
    }
    return review | facts


def test_years_are_read_from_either_way_of_writing_a_date():
    assert year("May 2019") == "2019"
    assert year("2019-05") == "2019"
    assert year(None) is None


def test_a_review_outside_the_library_still_has_its_own_facts():
    review = explorer_review(
        "B", source="HALT", in_library=False, loaded=False, csp=None, uploaded=None
    )
    assert review_facts(review) == [
        ("Published on", "HALT"),
        ("In the DHR Library", "No"),
        ("Loaded", "Skipped when loading"),
        ("CSP region", "South West"),
        ("Year of death", "2019"),
    ]


def test_facets_list_every_value_and_each_review_has_the_ids_of_its_own():
    catalogue = [
        ("About the victim / Sex", "Female"),
        ("About the victim / Sex", "Male"),  # no review has it, but it can still be chosen
        ("Filter by theme", "Victim - alcohol misuse"),
    ]
    details = [
        ("A", "About the victim / Sex", "Female"),
        ("A", "Filter by theme", "Victim - alcohol misuse"),
    ]
    reviews = [explorer_review("A"), explorer_review("B", source=None, in_library=False)]
    facets = build_facets(catalogue, details, reviews)

    assert [(f["section"], f["attribute"]) for f in facets] == [
        ("About the review", "Published on"),
        ("About the review", "In the DHR Library"),
        ("About the review", "Loaded"),
        ("About the review", "CSP region"),
        ("About the review", "Community Safety Partnership (CSP)"),
        ("About the review", "Year of death"),
        ("About the review", "Year uploaded"),
        ("About the victim", "Sex"),
        ("Themes", "Victim"),
    ]
    value_of = {v["id"]: (f["attribute"], v["value"]) for f in facets for v in f["values"]}
    assert len(value_of) == sum(len(f["values"]) for f in facets)  # every id is different
    assert ("Sex", "Male") in value_of.values()
    a = {value_of[i] for i in reviews[0]["tags"]}
    b = {value_of[i] for i in reviews[1]["tags"]}
    assert {
        ("Sex", "Female"),
        ("Victim", "alcohol misuse"),
        ("Published on", "Home Office website"),
    } <= a
    assert ("Sex", "Female") not in b
    assert {("Published on", "No link"), ("In the DHR Library", "No")} <= b
