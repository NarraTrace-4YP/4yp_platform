"""Tests for the O1 app's backend scripts (the database parts are checked against a real
database by hand), and for the load's progress bar."""

from f.dhr.O1.app_backend import get_status as o1_status
from f.dhr.O1.find_candidate_template_sections import get_candidates as o1_candidates
from f.dhr.O1.group_blocks_by_section import get_sections as o1_sections
from f.dhr.O1.link_case_metadata import get_attribute_counts as o1_attributes
from f.dhr.O1.load_reviews import load_reviews as load_dhrc
from tests.test_load_dhrc import NESTED, TEXT, make_dataset, make_review

# ----------------------------------------------------------------- o1_attributes


def test_attribute_counts_are_grouped_like_the_library_page():
    rows = [
        ("About the victim / Sex", "Female", 541, "DHRC-100", 82),
        ("About the victim / Sex", "Female", 541, "DHRC-RAW", 490),
        ("About the victim / Sex", "Male", 139, None, 0),  # none of ours
        ("About the death / Police force area / East", "Essex", 12, "DHRC-RAW", 3),
        ("Filter by theme", "Victim - alcohol misuse", 242, "DHRC-100", 30),
        ("Most filtered for DHRs", "Intimate-partner homicide", 452, None, 0),
    ]
    attributes = o1_attributes.group_counts(rows)
    assert [(a["section"], a["attribute"]) for a in attributes] == [
        ("Categories", ""),
        ("About the victim", "Sex"),
        ("About the death", "Police force area"),
        ("Themes", "Victim"),
    ]
    sex = attributes[1]["values"]
    assert sex[0] == {
        "value": "Female",
        "library": 541,
        "datasets": {"DHRC-100": 82, "DHRC-RAW": 490},
    }
    assert sex[1]["datasets"] == {"DHRC-100": 0, "DHRC-RAW": 0}
    assert attributes[2]["values"][0]["value"] == "East: Essex"


def test_review_fields_come_after_the_death_with_years_oldest_first():
    rows = [
        ("About the victim / Sex", "Female", 541, "DHRC-100", 82),
        ("About the death / Method of killing", "Stabbing", 300, "DHRC-100", 40),
        ("Filter by theme", "Victim - alcohol misuse", 242, "DHRC-100", 30),
        ("About the review / CSP region", "Greater London", 120, "DHRC-100", 20),
        ("About the review / Year of death", "2021", 50, None, 0),
        ("About the review / Year of death", "2019", 80, "DHRC-100", 9),
    ]
    attributes = o1_attributes.group_counts(rows)
    assert [(a["section"], a["attribute"]) for a in attributes] == [
        ("About the victim", "Sex"),
        ("About the death", "Method of killing"),
        ("About the review", "CSP region"),
        ("About the review", "Year of death"),
        ("Themes", "Victim"),
    ]
    assert [v["value"] for v in attributes[3]["values"]] == ["2019", "2021"]
    assert o1_attributes.as_totals({None: 684, "DHRC-100": 100}) == {
        "library": 684,
        "datasets": {"DHRC-100": 100, "DHRC-RAW": 0},
    }


# ----------------------------------------------------------------- o1_status


def test_status_lists_every_dataset_even_before_any_is_loaded():
    status = o1_status.describe({}, {}, None)
    assert [d["name"] for d in status["datasets"]] == ["DHRC-100", "DHRC-RAW"]
    assert all(d["version"] is None for d in status["datasets"])
    assert status["datasets"][1]["coverage"]["n_loaded"] == 0
    assert all(d["sections"] is None for d in status["datasets"])
    assert all(d["candidates"] == [] for d in status["datasets"])
    assert status["templates"] == []
    assert [d["headings"] for d in status["datasets"]] == ["checked", "datalab"]
    assert status["case_metadata"] is None


# ----------------------------------------------------------------- o1_sections


def test_outline_counts_every_block_inside_a_section():
    rows = [
        ("/page/0/SectionHeader/0", None, 1, "Introduction", 0, 9, 0, 1, 3),
        ("/page/1/SectionHeader/2", "/page/0/SectionHeader/0", 2, "Timescales", 5, 9, 1, 1, 4),
    ]
    first, second = o1_sections.outline(rows)
    assert first["n_inside"] == 9 and first["n_blocks"] == 3
    assert second["parent_id"] == "/page/0/SectionHeader/0" and second["n_inside"] == 4


def test_skipped_reviews_are_split_into_name_and_reason():
    problems = ["Annie - Hartlepool - 2018_a - DHR: heading /page/49/SectionHeader/0 comes first"]
    assert o1_sections.split_skipped(problems) == [
        {"doc_id": "Annie - Hartlepool - 2018_a - DHR",
         "problem": "heading /page/49/SectionHeader/0 comes first"}
    ]  # fmt: skip
    assert o1_sections.split_skipped(None) == []


# ----------------------------------------------------------------- o1_candidates


def candidate(heading_score, heading_rank, text_score, text_rank):
    return {"heading_score": heading_score, "heading_rank": heading_rank,
            "text_score": text_score, "text_rank": text_rank}  # fmt: skip


def test_a_candidate_says_how_it_was_found():
    assert o1_candidates.found_by(candidate(100, 1, 0.9, 1), 5, 60) == "both"
    assert o1_candidates.found_by(candidate(40, 1, 0.9, 2), 5, 60) == "text"  # heading too unlike
    assert o1_candidates.found_by(candidate(70, 3, 0.5, 9), 5, 60) == "heading"


def test_candidates_are_attached_to_their_sections_in_reading_order():
    sections = [
        ("a", None, 1, "Introduction", 0, 0, 0),
        ("b", "a", 2, "Terms of reference", 3, 1, 1),
        ("c", None, 1, "Glossary", 9, 4, 4),
    ]
    candidates = [
        ("b", "dhr-2016/imr", 3, "Terms of reference", 100.0, 1, 0.82, 1),
        ("a", "dhr-2016/overview-report", 3, "Introduction", 40.0, 1, 0.61, 2),
        ("b", "dhr-2016/overview-report", 6, "Terms of reference", 100.0, 2, 0.7, 2),
        ("b", "dhr-2016/overview-report", 4, "Timescales", 30.0, 9, 0.6, 3),
    ]
    result = o1_candidates.attach_candidates(sections, candidates, k=5, min_heading_score=60)
    assert [s["section_id"] for s in result] == ["a", "b", "c"]
    found = result[1]["candidates"]
    assert [c["template_position"] for c in found] == [3, 6, 4]
    assert [c["found_by"] for c in found] == ["both", "both", "text"]
    assert result[2]["candidates"] == []
    assert "section_id" not in result[0]["candidates"][0]
    # Equal heading scores are both best; only the highest text score is best by text.
    assert [c["best_by_heading"] for c in found] == [True, True, False]
    assert [c["best_by_text"] for c in found] == [True, False, False]
    # A heading match below min_heading_score is never best by heading.
    assert result[0]["candidates"][0]["best_by_heading"] is False


def test_template_sections_carry_how_often_they_are_the_best_match():
    rows = [
        ("dhr-2016/imr", 2, "IMR template", "individual management review", "u", 1, "Introduction",
         True, "", 30),
        ("dhr-2016/imr", 2, "IMR template", "individual management review", "u", 2, "Chronology",
         True, "What happened", 31),
        ("dhr-2016/action-plan", 5, "Action plan", "action plan", "u", 1, "Action plan", False,
         "", 44),
    ]  # fmt: skip
    counts = [("dhr-2016/imr", 2, 7, 3, 2)]
    templates = o1_candidates.attach_template_counts(rows, counts)
    assert [t["template"] for t in templates] == ["dhr-2016/imr", "dhr-2016/action-plan"]
    first, second = templates[0]["sections"]
    assert (first["n_best_by_text"], first["n_best_by_heading"]) == (0, 0)
    best = (second["n_best_by_text"], second["n_best_by_heading"], second["n_best_by_both"])
    assert best == (7, 3, 2)
    assert second["guidance"] == "What happened" and templates[1]["sections"][0]["printed"] is False


# ----------------------------------------------------------------- the load's progress bar


def test_load_progress_only_moves_forwards_and_stays_below_100(tmp_path):
    root = make_dataset(tmp_path, {"A": make_review([[TEXT]]), "B": make_review([[NESTED]])})
    seen = []
    assert load_dhrc.verify_checksums(root, seen.append) == []
    load_dhrc.read_documents(root, strict=True, progress=seen.append)
    assert seen == sorted(seen)
    assert load_dhrc.DOWNLOADED < seen[0] and seen[-1] == load_dhrc.READ
