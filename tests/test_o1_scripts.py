"""Tests for the O1 app's backend scripts (the database parts are checked against a real
database by hand), and for the load's progress bar."""

import sys
import types

import pytest

from f.dhr import load_dhrc, o1_attributes, o1_run_status, o1_status
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


# ----------------------------------------------------------------- o1_status


def test_status_lists_every_dataset_even_before_any_is_loaded():
    status = o1_status.describe({}, {}, None)
    assert [d["name"] for d in status["datasets"]] == ["DHRC-100", "DHRC-RAW"]
    assert all(d["version"] is None for d in status["datasets"])
    assert status["datasets"][1]["coverage"]["n_loaded"] == 0
    assert all(d["sections"] is None for d in status["datasets"])
    assert [d["headings"] for d in status["datasets"]] == ["checked", "datalab"]
    assert status["case_metadata"] is None


# ----------------------------------------------------------------- the load's progress bar


def test_load_progress_only_moves_forwards_and_stays_below_100(tmp_path):
    root = make_dataset(tmp_path, {"A": make_review([[TEXT]]), "B": make_review([[NESTED]])})
    seen = []
    assert load_dhrc.verify_checksums(root, seen.append) == []
    load_dhrc.read_documents(root, strict=True, progress=seen.append)
    assert seen == sorted(seen)
    assert load_dhrc.DOWNLOADED < seen[0] and seen[-1] == load_dhrc.READ
