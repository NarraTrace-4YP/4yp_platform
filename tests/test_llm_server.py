"""Tests for the llm_serve mailbox in f/dhr/blythe.py and the tool f/dhr/tools/llm_server.py.

Blythe needs Rowan's login, so LocalBlythe runs the same shell commands in a local folder
standing in for his home folder there: the commands themselves are tested, only SLURM is a
stand-in. The job's side (narratrace-slurm's llm_serve.py) has its own tests there; here its
answers are written by hand in the same format.
"""

import gzip
import json
import os
import stat
import subprocess

import pytest

from f.dhr import blythe
from f.dhr.blythe import BlytheError
from f.dhr.tools import llm_server as tool


class LocalBlythe(blythe.Blythe):
    """Blythe's commands, run in a local shell with `home` as the home folder."""

    def __init__(self, home):
        self.home = str(home)
        self.slurm_calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def _exec(self, command, stdin=b"", timeout=600):
        done = subprocess.run(
            ["bash", "-c", command],
            input=stdin,
            capture_output=True,
            cwd=self.home,
            env={**os.environ, "HOME": self.home},
            timeout=30,
        )
        return done.returncode, done.stdout, done.stderr

    def slurm(self, command):
        self.slurm_calls.append(command)
        if command.startswith("sbatch"):
            return 0, "1381999\n", ""
        return 0, "RUNNING|None|0:05:00\n---\n", ""


@pytest.fixture
def home(tmp_path):
    (tmp_path / "narratrace" / "jobs").mkdir(parents=True)
    (tmp_path / "narratrace" / "jobs" / "llm_serve.sbatch").write_text("#!/bin/bash\n")
    return tmp_path


@pytest.fixture
def b(home):
    return LocalBlythe(home)


def prompts_gz(*ids):
    lines = [
        json.dumps({"id": i, "messages": [{"role": "user", "content": "q"}], "labels": ["A"]})
        for i in ids
    ]
    return gzip.compress(("\n".join(lines) + "\n").encode())


def answer_as_the_job(run, request_id, answers):
    """Writes what llm_serve.py writes when it has answered a request."""
    outbox = run / "outbox"
    outbox.mkdir(exist_ok=True)
    with gzip.open(outbox / f"{request_id}.jsonl.gz", "wt") as f:
        for answer in answers:
            f.write(json.dumps(answer) + "\n")
    (outbox / f"{request_id}.json").write_text(
        json.dumps({"request": request_id, "status": "answered", "n_prompts": len(answers)})
    )
    for name in (f"{request_id}.jsonl.gz", f"{request_id}.callback"):
        (run / "inbox" / name).unlink(missing_ok=True)


def test_start_add_look_collect_and_stop(b, home):
    request = {"model": "Qwen/Qwen3.8-27B-FP8", "idle_minutes": 20}
    started = b.start_server("serve-1", request, hours=4)
    run = home / "narratrace" / "runs" / "serve-1"
    assert started == {"run_dir": str(run), "slurm_job_id": "1381999"}
    assert json.loads((run / "input" / "request.json").read_text()) == request
    assert (run / "slurm_job_id").read_text() == "1381999"
    sbatch = b.slurm_calls[0]
    assert "--time=04:00:00" in sbatch and sbatch.endswith("/narratrace/jobs/llm_serve.sbatch")

    added = b.add_request("serve-1", "r1", prompts_gz("a", "b"), "https://example.org/resume/1")
    assert added["request_id"] == "r1" and len(added["prompts_sha256"]) == 64
    assert sorted(p.name for p in (run / "inbox").iterdir()) == ["r1.callback", "r1.jsonl.gz"]
    assert stat.S_IMODE((run / "inbox" / "r1.callback").stat().st_mode) == 0o600
    assert gzip.decompress((run / "inbox" / "r1.jsonl.gz").read_bytes()).count(b"\n") == 2
    box = b.mailbox("serve-1")
    assert box["waiting"] == ["r1"] and box["answered"] == [] and box["slurm_job_id"] == "1381999"
    assert box["status"] is None and not box["stop_asked"]
    assert b.collect("serve-1", "r1") == {"state": "waiting"}
    with pytest.raises(BlytheError, match="already sent"):
        b.add_request("serve-1", "r1", prompts_gz("c"))

    (run / "status.json").write_text(json.dumps({"state": "waiting", "requests_done": 1}))
    answer_as_the_job(run, "r1", [{"id": "a", "answer": "A"}, {"id": "b", "answer": "A"}])
    box = b.mailbox("serve-1")
    assert (
        box["waiting"] == [] and box["answered"] == ["r1"] and box["status"]["state"] == "waiting"
    )
    got = b.collect("serve-1", "r1")
    assert got["state"] == "answered" and got["summary"]["n_prompts"] == 2
    assert tool.read_answers(got["results"])[1] == {"id": "b", "answer": "A"}
    with pytest.raises(BlytheError, match="already sent"):  # answered names stay used
        b.add_request("serve-1", "r1", prompts_gz("c"))

    (run / "outbox" / "r2.error.json").write_text(json.dumps({"status": "refused", "error": "x"}))
    assert b.mailbox("serve-1")["refused"] == ["r2"]
    assert b.collect("serve-1", "r2") == {
        "state": "refused",
        "error": {"status": "refused", "error": "x"},
    }

    b.stop_server("serve-1")
    assert (run / "stop").exists() and b.mailbox("serve-1")["stop_asked"]
    with pytest.raises(BlytheError, match="stop"):
        b.add_request("serve-1", "r3", prompts_gz("c"))


def test_no_hidden_file_is_left_and_nothing_is_overwritten(b, home):
    folder = home / "f"
    folder.mkdir()
    b.write_new(str(folder / "x.jsonl.gz"), b"first")
    assert [p.name for p in folder.iterdir()] == ["x.jsonl.gz"]
    with pytest.raises(BlytheError, match="mustn't exist"):
        b.write_new(str(folder / "x.jsonl.gz"), b"second")
    assert (folder / "x.jsonl.gz").read_bytes() == b"first"


def test_requests_only_go_to_a_server_that_is_running(b, home):
    run = home / "narratrace" / "runs"
    (run / "batch-1" / "input").mkdir(parents=True)  # an llm_batch run: nothing reads an inbox
    with pytest.raises(BlytheError, match="start_server"):
        b.add_request("batch-1", "r1", prompts_gz("a"))
    with pytest.raises(BlytheError, match="isn't on Blythe"):
        b.add_request("nowhere", "r1", prompts_gz("a"))
    b.start_server("serve-2", {"model": "m"}, hours=1)
    (run / "serve-2" / "status.json").write_text(json.dumps({"state": "stopped"}))
    with pytest.raises(BlytheError, match="start a new one"):
        b.add_request("serve-2", "r1", prompts_gz("a"))


@pytest.mark.parametrize("bad", ["", "x; rm -rf ~", "../x", "a.b", "a b", "-x", "x" * 65, None])
def test_request_names_are_checked(b, bad):
    with pytest.raises(BlytheError):
        blythe.check_request_id(bad)
    with pytest.raises(BlytheError):
        b.collect("serve-1", bad)


def test_llm_serve_is_a_job_windmill_may_run():
    blythe.check_job("llm_serve")
    assert blythe.parse_mailbox("[inbox]\nr1.jsonl.gz\n.r2.jsonl.gz.tmp\n[outbox]\n")[
        "waiting"
    ] == ["r1"]
    with pytest.raises(BlytheError, match="isn't on Blythe"):
        blythe.parse_mailbox("")  # cd failed: nothing listed


# ---------------------------------------------------------------- the tool script


@pytest.fixture
def rowan(monkeypatch):
    monkeypatch.setenv("WM_USERNAME", "rshields2004")


def test_prompts_in_either_form(rowan):
    got = tool.normalise(
        [
            {"question": "Which?", "labels": ["A", "B"], "system": "Answer with a letter."},
            {"id": "own", "messages": [{"role": "user", "content": "x"}], "labels": ["Z"]},
        ]
    )
    assert got[0]["id"] == "p1" and got[0]["messages"] == [
        {"role": "system", "content": "Answer with a letter."},
        {"role": "user", "content": "Which?"},
    ]
    assert got[1]["id"] == "own" and got[1]["meta"] is None
    for bad in (
        [],
        "not a list",
        [{"question": "", "labels": ["A"]}],
        [{"question": "q", "labels": []}],
        [{"question": "q", "labels": ["A"], "model": "other"}],
        [{"messages": [{"role": "tool", "content": "x"}], "labels": ["A"]}],
        [
            {"id": "x", "question": "q", "labels": ["A"]},
            {"id": "x", "question": "q", "labels": ["A"]},
        ],
    ):
        with pytest.raises(ValueError):
            tool.normalise(bad)


def test_only_rowan_can_use_it_even_for_a_dry_run(monkeypatch):
    monkeypatch.setenv("WM_USERNAME", "ks0610")
    with pytest.raises(BlytheError, match="Only rshields2004"):
        tool.main("status", run_id="serve-1", dry_run=True)


def test_dry_runs_contact_nothing(rowan, monkeypatch):
    monkeypatch.setattr(tool, "Blythe", lambda: pytest.fail("a dry run logged in"))
    started = tool.main("start", prompts=[{"question": "q", "labels": ["A"]}], dry_run=True)
    assert started["request.json"]["model"] == tool.MODEL
    assert started["request.json"]["idle_minutes"] == 20 and started["n_prompts"] == 1
    assert started["run_id"].startswith("serve-")
    added = tool.main(
        "add prompts", run_id="serve-1", prompts=[{"question": "q", "labels": ["A"]}], dry_run=True
    )
    assert added["n_prompts"] == 1 and added["request_id"].startswith("r")
    assert "Dry run" in tool.main("stop", run_id="serve-1", dry_run=True)["result"]
    for bad in ({"hours": 0}, {"hours": 49}, {"idle_minutes": 0}, {"max_model_len": 100}):
        with pytest.raises(ValueError):
            tool.main("start", dry_run=True, **bad)
    with pytest.raises(ValueError, match="run_id"):
        tool.main("status")


def test_the_tool_end_to_end(rowan, monkeypatch, home):
    local = LocalBlythe(home)
    monkeypatch.setattr(tool, "Blythe", lambda: local)
    started = tool.main(
        "start",
        run_id="serve-9",
        request_id="first",
        prompts=[
            {"question": "q1", "labels": ["A", "B"]},
            {"question": "q2", "labels": ["A", "B"]},
        ],
        hours=2,
        idle_minutes=30,
    )
    assert started["slurm_job_id"] == "1381999" and started["first_request"]["n_prompts"] == 2
    run = home / "narratrace" / "runs" / "serve-9"
    request = json.loads((run / "input" / "request.json").read_text())
    assert request["idle_minutes"] == 30 and request["model"] == tool.MODEL
    added = tool.main(
        "add prompts", run_id="serve-9", prompts=[{"question": "q3", "labels": ["A"]}]
    )
    second = added["request_id"]

    status = tool.main("status", run_id="serve-9")
    assert (
        status["waiting"] == sorted(["first", second]) and status["slurm_job"]["state"] == "RUNNING"
    )
    assert tool.main("collect", run_id="serve-9", request_id="first")["state"] == "waiting"

    answer_as_the_job(
        run,
        "first",
        [
            {"id": "p1", "answer": "B", "probabilities": {"B": 0.9}, "error": None, "meta": None},
            {"id": "p2", "answer": "A", "probabilities": {"A": 0.7}, "error": None, "meta": None},
        ],
    )
    got = tool.main("collect", run_id="serve-9", request_id="first")
    assert got["state"] == "answered" and [a["answer"] for a in got["answers"]] == ["B", "A"]
    assert tool.main("stop", run_id="serve-9")["run_id"] == "serve-9"
    assert (run / "stop").exists()
