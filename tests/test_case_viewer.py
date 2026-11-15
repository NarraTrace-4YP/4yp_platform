"""Tests for the case viewer's scripts (the database parts are checked by hand on the server)."""

from f.dhr.case_viewer_details import group_details, split_detail


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
