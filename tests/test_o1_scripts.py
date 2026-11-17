"""Tests for the O1 app's backend scripts (the database parts are checked against a real
database by hand), and for the load's progress bar."""

import sys
import types

import pytest

from f.dhr import load_dhrc, o1_attributes, o1_candidates, o1_run_status, o1_sections, o1_status
from tests.test_load_dhrc import NESTED, TEXT, make_dataset, make_review

# ----------------------------------------------------------------- o1_run_status

LOG = "job=1 tag=python3\n\nDownloading...\n\nOK    Downloaded\n" + "".join(
    f"line {n}\n" for n in range(60)
)


def test_job_states():
    assert o1_run_status.job_state({"type": "QueuedJob", "running": False}) == "queued"
    assert o1_run_status.job_state({"type": "QueuedJob", "running": True}) == "running"
    assert o1_run_status.job_state({"type": "CompletedJob", "success": True}) == "done"
    assert o1_run_status.job_state({"type": "CompletedJob", "success": False}) == "failed"
    cancelled = {"type": "CompletedJob", "success": False, "canceled": True}
    assert o1_run_status.job_state(cancelled) == "cancelled"


def test_log_tail_keeps_the_last_lines_without_blanks():
    tail = o1_run_status.log_tail(LOG)
    assert len(tail) == o1_run_status.LOG_LINES
    assert tail[-1] == "line 59"
    assert "" not in tail
    assert o1_run_status.log_tail(None) == []


def test_failed_job_gives_its_error_message():
    result = {"error": {"name": "LoadError", "message": "found 3 reviews, expected 100"}}
    assert o1_run_status.error_message(result) == "found 3 reviews, expected 100"
    assert o1_run_status.error_message({"n_documents": 3}) is None


FLOW_JOB = {
    "id": "flow-1",
    "type": "QueuedJob",
    "running": True,
    "job_kind": "flow",
    "script_path": "f/dhr/o1_pipeline",
    "logs": "",
    "flow_status": {
        "modules": [
            {"id": "load_dhrc_100", "type": "Success", "job": "a"},
            {"id": "load_dhrc_raw", "type": "InProgress", "job": "b"},
            {"id": "collect_case_metadata", "type": "WaitingForPriorSteps"},
            {"id": "status", "type": "WaitingForPriorSteps"},
        ]
    },
}


def test_flow_steps_in_plain_words():
    steps = o1_run_status.flow_steps(FLOW_JOB)
    assert [s["state"] for s in steps] == ["done", "running", "waiting", "waiting"]
    assert o1_run_status.current_step(steps)["id"] == "load_dhrc_raw"


def test_skipped_step_is_shown_as_skipped():
    job = {"flow_status": {"modules": [{"id": "x", "type": "Success", "skipped": True}]}}
    assert o1_run_status.flow_steps(job)[0]["state"] == "skipped"


def test_after_a_flow_the_last_step_that_ran_is_shown():
    steps = [
        {"id": "a", "state": "done", "job_id": "1"},
        {"id": "b", "state": "failed", "job_id": "2"},
        {"id": "c", "state": "waiting", "job_id": None},
    ]
    assert o1_run_status.current_step(steps)["id"] == "b"


@pytest.fixture
def fake_wmill(monkeypatch):
    """Stands in for Windmill's client, with the jobs given in .jobs."""
    module = types.SimpleNamespace(jobs={}, progress={})
    module.get_job = lambda job_id: module.jobs[job_id]
    module.get_progress = lambda job_id: module.progress.get(job_id)
    monkeypatch.setitem(sys.modules, "wmill", module)
    return module


def test_running_flow_shows_the_running_steps_log_and_progress(fake_wmill):
    fake_wmill.jobs = {
        "flow-1": FLOW_JOB,
        "b": {"id": "b", "type": "QueuedJob", "running": True, "logs": "OK    Downloaded\n"},
    }
    fake_wmill.progress = {"b": 40}
    summary = o1_run_status.main("flow-1")
    assert summary["state"] == "running"
    assert summary["current_step"] == "load_dhrc_raw"
    assert summary["progress"] == 40
    assert summary["log"] == ["OK    Downloaded"]
    assert len(summary["steps"]) == 4


def test_finished_script_gives_its_result(fake_wmill):
    fake_wmill.jobs = {
        "j": {
            "id": "j",
            "type": "CompletedJob",
            "success": True,
            "script_path": "f/dhr/load_dhrc",
            "logs": LOG,
            "result": {"n_documents": 100},
            "duration_ms": 61000,
        }
    }
    summary = o1_run_status.main("j")
    assert summary["state"] == "done"
    assert summary["result"] == {"n_documents": 100}
    assert summary["steps"] is None
    assert summary["progress"] is None


def test_only_o1_runs_can_be_read(fake_wmill):
    fake_wmill.jobs = {"x": {"id": "x", "type": "CompletedJob", "script_path": "u/someone/x"}}
    with pytest.raises(ValueError, match="isn't an O1 pipeline run"):
        o1_run_status.main("x")


def test_progress_problems_never_fail_the_check(fake_wmill):
    def broken(job_id):
        raise RuntimeError("no progress here")

    fake_wmill.get_progress = broken
    assert o1_run_status.progress_of("j") is None


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
    result = o1_candidates.attach(sections, candidates, k=5, min_heading_score=60)
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
    templates = o1_candidates.with_counts(rows, counts)
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
