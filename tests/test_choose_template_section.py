"""Tests for O1 stage 5: f/dhr/O1/choose_template_section and the Blythe client f/dhr/blythe.py.

The prompts, the reading of answers and the SLURM bookkeeping are checked here. Talking to
Blythe needs Rowan's login, so that is checked in Windmill. The database part runs only when
DHR_TEST_DATABASE_URL points at an empty test database (it applies every migration there).
"""

import asyncio
import gzip
import io
import json
import os
import sys
import types

import pytest

try:
    import wmill  # noqa: F401 - only installed in Windmill
except ImportError:  # stand-ins for the workflow decorators, which only mark functions
    stub = types.ModuleType("wmill")
    stub.task = lambda func=None, **_: func if func else (lambda f: f)
    stub.workflow = lambda func: func
    for name in ("get_approval_urls", "step", "wait_for_approval"):
        setattr(stub, name, lambda *args, **kwargs: None)
    sys.modules["wmill"] = stub

from f.dhr import blythe  # noqa: E402
from f.dhr.O1.choose_template_section import choose_template_section as stage  # noqa: E402
from f.dhr.O1.choose_template_section import refine as refining  # noqa: E402
from f.dhr.O1.choose_template_section.prompts import (  # noqa: E402
    LETTERS,
    NONE,
    PROMPT_VERSION,
    Document,
    Section,
    Template,
    TemplateSection,
    action_plans,
    document_prompt,
    extra_prompt,
    interpret,
    interpret_document,
    section_options,
    section_prompt,
    shorten,
)

GUIDANCE = "Multi-agency Statutory Guidance (2016)"


def template(name, appendix, kind, headings, title=None):
    t = Template(name, appendix, title or name.split("/")[1].title(), kind)
    t.sections = [
        TemplateSection(name, i + 1, h, f"Guidance on {h.lower()}.") for i, h in enumerate(headings)
    ]
    return t


REPORT = template(
    "dhr-2016/overview-report", 3, "overview report", ["Timescales", "Chronology", "Conclusions"]
)
SUMMARY = template("dhr-2016/executive-summary", 4, "executive summary", ["The review process"])
PLAN = template("dhr-2016/action-plan", 5, "action plan", ["Recommendation"])
IMR = template("dhr-2016/imr", 2, "individual management review", ["Introduction"])
TEMPLATES = [PLAN, SUMMARY, REPORT, IMR]  # not in the guidance's order on purpose

SECTION = Section(
    "Mary - Bromley - 2018_a - DHR",
    "/page/5/SectionHeader/8",
    "1.3 Timescales",
    ["1 Introduction"],
    "The review started in May 2018 and ended in March 2019.",
    "",
)
DOCUMENT = Document(
    "Mary - Bromley - 2018_a - DHR",
    "Domestic Homicide Review Overview Report into the death of Mary",
    [(1, "1 Introduction"), (2, "1.3 Timescales"), (1, "2 Chronology")],
)


# ---------------------------------------------------------------- prompts


def test_shorten_cuts_at_a_word_and_joins_lines():
    assert shorten("a  b\n\nc", 100) == "a b c"
    cut = shorten("one two three four five six", 15)
    assert cut.endswith("…") and len(cut) <= 15 and " " in cut and "thre…" not in cut
    assert shorten("", 10) == "" and shorten(None, 10) == ""


def test_document_prompt_offers_report_templates_in_the_guidances_order():
    prompt = document_prompt(DOCUMENT, TEMPLATES, GUIDANCE)
    # the action plan goes with a report rather than being one, so it isn't offered
    assert prompt["meta"]["options"] == {
        "A": "dhr-2016/imr",
        "B": "dhr-2016/overview-report",
        "C": "dhr-2016/executive-summary",
    }
    assert prompt["labels"] == ["A", "B", "C", NONE]
    assert json.loads(prompt["id"]) == [DOCUMENT.doc_id]
    user = prompt["messages"][1]["content"]
    assert "Timescales; Chronology; Conclusions" in user
    assert "Mary - Bromley" in user and "Domestic Homicide Review Overview Report" in user
    assert "1 Introduction" in user and "  1.3 Timescales" in user  # headings indented by level
    assert f"{NONE}. None of these" in user and user.endswith("Answer with the letter only.")
    assert GUIDANCE in user and prompt["messages"][0]["role"] == "system"


def test_document_prompt_hints_only_name_kinds_the_guidance_has():
    user = document_prompt(DOCUMENT, TEMPLATES, GUIDANCE)["messages"][1]["content"]
    assert "executive summary are separate" in user and "one agency's own report" in user
    dardr = [template("dardr-2026/dardr", 1, "overview report", ["Executive summary"]), PLAN]
    user = document_prompt(DOCUMENT, dardr, "G")["messages"][1]["content"]
    assert "executive summary are separate" not in user and "agency's own" not in user
    assert "Judge by the document's structure" in user


def test_section_options_are_the_template_then_the_action_plan():
    options = section_options(REPORT, action_plans(TEMPLATES))
    assert [(t.template, s.position) for t, s in options] == [
        ("dhr-2016/overview-report", 1),
        ("dhr-2016/overview-report", 2),
        ("dhr-2016/overview-report", 3),
        ("dhr-2016/action-plan", 1),
    ]
    # a review that is itself an action plan isn't offered it twice
    assert len(section_options(PLAN, action_plans(TEMPLATES))) == 1


def test_section_prompt_letters_every_template_section():
    prompt = section_prompt(SECTION, REPORT, action_plans(TEMPLATES), GUIDANCE)
    assert prompt["labels"] == ["A", "B", "C", "D", NONE]
    assert json.loads(prompt["id"]) == [SECTION.doc_id, SECTION.section_id]
    assert prompt["meta"]["options"]["B"] == ["dhr-2016/overview-report", 2]
    assert prompt["meta"]["options"]["D"] == ["dhr-2016/action-plan", 1]
    user = prompt["messages"][1]["content"]
    assert "A. Timescales (Guidance on timescales.)" in user
    assert "Action-Plan:\nD. Recommendation" in user
    assert "even if others belong to it too" in user  # several sections may share one
    assert "Inside: 1 Introduction" in user and "Heading: 1.3 Timescales" in user
    assert user.endswith("Answer with the letter only.")


def test_section_prompts_of_one_template_start_alike():
    other = Section("Other - Place - 2019_a - DHR", "s", "Chronology", [], "In 2017...", "")
    a = section_prompt(SECTION, REPORT, action_plans(TEMPLATES), GUIDANCE)["messages"][1]
    b = section_prompt(other, REPORT, action_plans(TEMPLATES), GUIDANCE)["messages"][1]
    shared = a["content"].index("REPORT SECTION")
    assert a["content"][:shared] == b["content"][:shared]  # vLLM reuses the shared start


def test_section_prompt_for_a_section_with_only_subsections():
    section = Section("D", "/page/1/SectionHeader/0", "Appendices", [], "", "Glossary; Action plan")
    user = section_prompt(section, REPORT, [], "G")["messages"][1]["content"]
    assert "Inside:" not in user and "and of the action plan" not in user
    assert "Its subsections: Glossary; Action plan" in user
    assert "(no text of its own, only subsections)" in user


def test_prompts_need_options_and_enough_letters():
    with pytest.raises(ValueError):
        section_prompt(SECTION, template("t/empty", 1, "overview report", []), [], "G")
    big = template("t/big", 1, "overview report", [f"H{i}" for i in range(len(LETTERS) + 1)])
    with pytest.raises(ValueError):
        section_prompt(SECTION, big, [], "G")


def test_prompt_text_is_limited():
    long = Section("D", "s", "H" * 500, ["P" * 500], "word " * 2000, "S " * 1000)
    report = template("t/r", 1, "overview report", [f"Heading {i} " * 30 for i in range(20)])
    for s in report.sections:
        s.guidance = "g " * 2000
    user = section_prompt(long, report, [PLAN], "G")["messages"][1]["content"]
    assert len(user) < 12_000  # fits the job's 8192-token limit with room to spare
    many = Document("D", "o " * 5000, [(1, "heading " * 40)] * 400)
    user = document_prompt(many, TEMPLATES, "G")["messages"][1]["content"]
    assert len(user) < 12_000


def section_answer(letter, probabilities=None, error=None):
    meta = {"doc_id": "D", "section_id": "s", "options": {"A": ["t", 3], "B": ["t", 7]}}
    return {
        "id": "x",
        "meta": meta,
        "answer": letter,
        "probabilities": probabilities or {},
        "error": error,
    }


def document_answer(letter, probabilities=None, error=None, doc="D"):
    meta = {"doc_id": doc, "options": {"A": "t/imr", "B": "t/report"}}
    return {
        "id": "x",
        "meta": meta,
        "answer": letter,
        "probabilities": probabilities or {},
        "error": error,
    }


def test_interpret_a_section_letter_none_and_no_answer():
    row = interpret(section_answer("B", {"A": 0.1, "B": 0.8, "Z": 0.05}))
    assert (row["template"], row["template_position"], row["probability"]) == ("t", 7, 0.8)
    assert row["none_probability"] == 0.05 and row["n_options"] == 2 and row["error"] is None
    assert row["probabilities"] == {"t#3": 0.1, "t#7": 0.8, "none": 0.05}

    row = interpret(section_answer("Z", {"Z": 0.9}))
    assert row["template"] is None and row["answer"] == "Z" and row["probability"] == 0.9

    row = interpret(section_answer(None, error="prompt too long (9000 tokens)"))
    assert row["template"] is None and row["answer"] is None and row["probability"] is None
    assert row["error"].startswith("prompt too long")

    row = interpret(section_answer("Q", {"Q": 0.9}))  # not an option: treated as no answer
    assert row["answer"] is None and "isn't one of the options" in row["error"]


def test_interpret_a_document_answer():
    row = interpret_document(document_answer("B", {"A": 0.1, "B": 0.85, "Z": 0.02}))
    assert (row["template"], row["answer"], row["probability"]) == ("t/report", "B", 0.85)
    assert row["probabilities"] == {"t/imr": 0.1, "t/report": 0.85, "none": 0.02}
    row = interpret_document(document_answer("Z", {"Z": 0.7}))
    assert row["template"] is None and row["answer"] == "Z" and row["probability"] == 0.7
    row = interpret_document(document_answer(None, error="prompt too long"))
    assert row["template"] is None and row["answer"] is None and row["error"]


def test_document_rows_cover_every_review_once():
    documents = [Document("D", "", []), Document("E", "", [])]
    rows = stage.document_rows([document_answer("B", {"B": 1.0})], documents)
    assert [r["doc_id"] for r in rows] == ["D", "E"]
    assert rows[0]["template"] == "t/report" and rows[1]["error"].startswith("no result")
    with pytest.raises(ValueError):
        stage.document_rows([document_answer("A", doc="F")], documents)


def test_choice_rows_cover_every_section_once_and_say_why_one_wasnt_asked():
    sections = [
        Section("D", "s", "h", [], "", ""),
        Section("D", "t", "h", [], "", ""),
        Section("E", "s", "h", [], "", ""),
        Section("F", "s", "h", [], "", ""),
    ]
    documents = {
        "D": {"template": "t", "answer": "B"},
        "E": {"template": None, "answer": NONE},  # matched none of the templates
        "F": {"template": None, "answer": None},  # no answer for its template
    }
    rows = stage.choice_rows([section_answer("A", {"A": 1.0})], sections, documents)
    assert [(r["doc_id"], r["section_id"]) for r in rows] == [
        ("D", "s"),
        ("D", "t"),
        ("E", "s"),
        ("F", "s"),
    ]
    assert rows[0]["template"] == "t" and rows[1]["error"].startswith("no result")
    assert rows[2]["error"] == stage.NOT_ASKED and rows[3]["error"] == stage.NO_DOCUMENT
    tally = stage.counts([{"doc_id": d, **v} for d, v in documents.items()], rows)
    assert tally == {
        "n_reviews": 3,
        "n_reviews_matched": 1,
        "n_sections": 4,
        "n_matched": 1,
        "n_unmatched": 1,
        "n_no_answer": 2,
        "n_reviews_doubtful": 0,
        "n_doubtful": 0,
    }
    with pytest.raises(ValueError):
        stage.choice_rows([section_answer("A")], sections[1:], documents)  # not asked for


def test_an_unsure_none_falls_back_to_the_likeliest_template():
    rows = [
        {
            "doc_id": "D",
            "template": None,
            "answer": NONE,
            "probability": 0.6,
            "probabilities": {"none": 0.6, "t/report": 0.17, "t/summary": 0.19},
        },
        {
            "doc_id": "E",
            "template": None,
            "answer": NONE,
            "probability": 0.9,
            "probabilities": {"none": 0.9, "t/report": 0.05},
        },
        {
            "doc_id": "F",
            "template": "t/report",
            "answer": "B",
            "probability": 0.99,
            "probabilities": {"t/report": 0.99},
        },
    ]
    stage.apply_fallback(rows)
    assert (rows[0]["template"], rows[0]["fallback"]) == ("t/summary", True)
    assert rows[0]["answer"] == NONE and rows[0]["probability"] == 0.6  # what the model said
    assert (rows[1]["template"], rows[1]["fallback"]) == (None, False)  # sure: stays unmatched
    assert (rows[2]["template"], rows[2]["fallback"]) == ("t/report", False)


def test_a_fallback_review_and_all_its_sections_are_doubtful():
    documents = [
        {"doc_id": "D", "template": "t", "answer": NONE, "probability": 0.6, "fallback": True},
        {"doc_id": "E", "template": "t", "answer": "A", "probability": 0.99, "fallback": False},
    ]
    sections = [Section(d, s, "h", [], "", "") for d in "DE" for s in "st"]
    answers = [section_answer("A", {"A": 0.95})]
    answers[0]["meta"] = {**answers[0]["meta"], "doc_id": "D", "section_id": "s"}
    more = [
        {
            **section_answer(letter, {letter: p}),
            "meta": {"doc_id": d, "section_id": sid, "options": {"A": ["t", 3], "B": ["t", 7]}},
        }
        for d, sid, letter, p in [("D", "t", "B", 0.9), ("E", "s", "A", 0.4), ("E", "t", "B", 0.9)]
    ]
    rows = stage.choice_rows(answers + more, sections, {d["doc_id"]: d for d in documents})
    tally = stage.counts(documents, rows)
    assert tally["n_reviews_doubtful"] == 1 and tally["n_matched"] == 4
    assert tally["n_doubtful"] == 3  # both of D's sections, and E's 40% one


def test_section_prompts_only_for_reviews_with_a_template():
    inputs = {
        "templates": {t.template: t for t in TEMPLATES},
        "sections": [SECTION, Section("Other", "s", "Glossary", [], "", "")],
        "guidance_title": GUIDANCE,
    }
    prompts = stage.section_prompts(
        inputs, {SECTION.doc_id: "dhr-2016/overview-report", "Other": None}
    )
    assert [p["meta"]["doc_id"] for p in prompts] == [SECTION.doc_id]


def test_prompts_file_is_the_same_bytes_every_time():
    rows = [{"id": "a", "messages": []}, {"id": "b", "messages": []}]
    assert stage.gzip_lines(rows) == stage.gzip_lines(rows)
    assert stage.read_results(stage.gzip_lines(rows)) == rows


def test_read_results_keeps_complete_lines_of_a_cut_off_file():
    raw = stage.gzip_lines([{"id": str(i)} for i in range(100)])
    results = stage.read_results(raw[: len(raw) - 20])
    assert results and all(r == {"id": str(i)} for i, r in enumerate(results))


def test_automatic_time_limit():
    assert stage.automatic_hours(0) == 1
    assert stage.automatic_hours(50_000) == 2  # the pilot reviews' document prompts
    assert stage.automatic_hours(100_000_000) == 12
    assert stage.automatic_hours(10_000_000_000) == blythe.MAX_HOURS


def test_pilot_is_the_specifications_ten():
    assert len(stage.PILOT_REVIEWS) == len(set(stage.PILOT_REVIEWS)) == 10


def test_checks_refuse_unknown_settings():
    stage.check_choices("DHRC-100", "dhr-2016", "pilot", "Qwen/Qwen3.8-27B-FP8")
    for bad in [
        ("X", "dhr-2016", "all", "Qwen/Qwen3.8-27B-FP8"),
        ("DHRC-100", "dhr-2017", "all", "Qwen/Qwen3.8-27B-FP8"),
        ("DHRC-100", "dhr-2016", "some", "Qwen/Qwen3.8-27B-FP8"),
        ("DHRC-100", "dhr-2016", "all", "gpt"),
    ]:
        with pytest.raises(ValueError):
            stage.check_choices(*bad)


def test_run_ids_are_safe_folder_names():
    run_id = stage.new_run_id("DHRC-RAW", "dardr-2026", "all")
    blythe.check_name(run_id)
    assert run_id.startswith("o1-choose-dhrc-raw-dardr-2026-all-")


# ---------------------------------------------------------------- the Blythe client


def test_only_rowan_can_use_blythe(monkeypatch):
    monkeypatch.setenv("WM_USERNAME", "rshields2004")
    blythe.check_owner()
    for other in ("ks0610", "admin2", "deploy", ""):
        monkeypatch.setenv("WM_USERNAME", other)
        with pytest.raises(blythe.BlytheError, match="Only rshields2004"):
            blythe.check_owner()
    monkeypatch.delenv("WM_USERNAME")
    with pytest.raises(blythe.BlytheError):
        blythe.check_owner()


def test_the_workflow_refuses_other_users_before_anything_else(monkeypatch):
    monkeypatch.setenv("WM_USERNAME", "ks0610")
    with pytest.raises(blythe.BlytheError, match="Only rshields2004"):
        asyncio.run(stage.main(dry_run=True))


def test_names_jobs_and_time_limits_are_checked():
    for good in ("o1-choose-dhrc-100-dhr-2016-pilot-20261119-150000-ab12", "message.txt"):
        blythe.check_name(good)
    for bad in ("../x", "a b", "", "-x", "a;rm", "a/b"):
        with pytest.raises(blythe.BlytheError):
            blythe.check_name(bad)
    blythe.check_job("llm_batch")
    with pytest.raises(blythe.BlytheError):
        blythe.check_job("anything_else")
    for bad in ("123; rm -rf ~", "", "12a"):
        with pytest.raises(blythe.BlytheError):
            blythe.check_job_id(bad)
    assert blythe.time_limit(None) is None and blythe.time_limit(7) == "07:00:00"
    for bad in (0, 49, 2.5, "3"):
        with pytest.raises(blythe.BlytheError):
            blythe.time_limit(bad)


def test_sbatch_command_and_its_answer():
    command = blythe.sbatch_command(
        "/h/narratrace/jobs/llm_batch.sbatch", "/h/narratrace/runs/r1", 26
    )
    assert command == (
        "sbatch --parsable --time=26:00:00 --chdir /h/narratrace/runs/r1"
        " /h/narratrace/jobs/llm_batch.sbatch"
    )
    assert "--time" not in blythe.sbatch_command("/j", "/r")
    assert blythe.parse_sbatch("1374161\n") == "1374161"
    assert blythe.parse_sbatch("Welcome!\n1374161;blythe\n") == "1374161"
    assert blythe.parse_sbatch("sbatch: error: invalid partition\n") is None


@pytest.mark.parametrize(
    "out, finished, state",
    [
        ("PENDING|Priority|0:00\n---\nPENDING|0:0|00:00:00|None assigned\n", False, "PENDING"),
        ("RUNNING|None|1:02:03\n---\nRUNNING|0:0|01:02:03|sea010\n", False, "RUNNING"),
        ("---\nCOMPLETED|0:0|00:00:01|sea010\n", True, "COMPLETED"),
        ("---\nTIMEOUT|0:0|24:00:12|sea004\n", True, "TIMEOUT"),
        ("---\nCANCELLED by 12345|0:0|00:00:00|None assigned\n", True, "CANCELLED by 12345"),
        ("COMPLETING|None|0:30\n---\nCOMPLETED|0:0|00:00:30|sea010\n", False, "COMPLETING"),
        ("---\n", False, "UNKNOWN (not listed by SLURM yet)"),
    ],
)
def test_job_state(out, finished, state):
    parsed = blythe.parse_state(out)
    assert parsed["finished"] is finished and parsed["state"] == state


def test_state_command_only_takes_job_numbers():
    assert "squeue -h -j 1374161" in blythe.state_command("1374161")
    with pytest.raises(blythe.BlytheError):
        blythe.state_command("1; touch x")


# ---------------------------------------------------------------- 3. refining the unmatched


def test_extra_sections_per_guidance_keep_their_positions():
    old, new = refining.extra_template("dhr-2016"), refining.extra_template("dardr-2026")
    assert old.kind == new.kind == refining.EXTRA_KIND
    assert [s.position for s in new.sections] == list(range(1, len(new.sections) + 1))
    headings_old = [s.heading for s in old.sections]
    assert "Equality and diversity" not in headings_old  # the 2016 template has its own
    assert "Equality and diversity" in [s.heading for s in new.sections]
    assert headings_old[0] == "Front matter" and len(new.sections) <= len(LETTERS)


def test_extra_prompt_letters_the_extra_sections():
    extras = refining.extra_template("dhr-2016")
    prompt = extra_prompt(SECTION, REPORT, extras, GUIDANCE)
    text = prompt["messages"][1]["content"]
    assert "Overview-Report" in text and "A. Front matter (" in text
    assert f"{NONE}. None of these" in text and SECTION.heading in text
    assert prompt["labels"][-1] == NONE and len(prompt["labels"]) == len(extras.sections) + 1
    assert prompt["meta"]["options"]["A"] == ["dhr-2016/extra", 1]
    answer = interpret(
        {"answer": "C", "probabilities": {"C": 0.7, NONE: 0.2}, "meta": prompt["meta"]}
    )
    assert (answer["template"], answer["template_position"]) == ("dhr-2016/extra", 3)


def choice(section, template=None, position=None, answer="A", p=0.9, none=0.05, ps=None):
    return {
        "doc_id": "D",
        "section_id": section,
        "template": template,
        "template_position": position,
        "answer": answer,
        "probability": p,
        "none_probability": none,
        "probabilities": ps or {},
    }


T = "g/report"
# s0 matched; s1 (in s0) none; s2 (in s1) none; s3 top-level none; s4 (in s3) none;
# s5 top-level none, unsure; s6 top-level none, sure.
ROWS = [
    choice("s0", T, 1, "A", 0.4),
    choice("s1", answer=NONE, p=0.9, none=0.9),
    choice("s2", answer=NONE, p=0.9, none=0.9),
    choice("s3", answer=NONE, p=0.9, none=0.9),
    choice("s4", answer=NONE, p=0.9, none=0.9),
    choice("s5", answer=NONE, p=0.6, none=0.6, ps={"none": 0.6, f"{T}#2": 0.3, f"{T}#1": 0.1}),
    choice("s6", answer=NONE, p=0.95, none=0.95, ps={"none": 0.95, f"{T}#2": 0.05}),
]
PARENTS = {
    ("D", s): p
    for s, p in [
        ("s0", None),
        ("s1", "s0"),
        ("s2", "s1"),
        ("s3", None),
        ("s4", "s3"),
        ("s5", None),
        ("s6", None),
    ]
}
DOCS = {"D": {"doc_id": "D", "template": T}}
X = "g/extra"  # guidance "g" has no dardr-only extras: Appendices 8, Report part 9, Other 10
APPENDICES, REPORT_PART, OTHER = 8, 9, 10


def test_only_sections_inside_no_matched_section_or_doubtful_matches_are_asked():
    # s0: a doubtful match (40%); s1 and s2 sit inside it, so they go with it instead
    assert refining.to_ask(ROWS, PARENTS, DOCS) == [
        ("D", s) for s in ("s0", "s3", "s4", "s5", "s6")
    ]
    assert refining.to_ask(ROWS, PARENTS, {"D": {"doc_id": "D", "template": None}}) == []


def test_refining_parent_then_extra_then_nearest():
    extras = {
        ("D", "s3"): {
            "answer": "B",
            "probability": 0.7,
            "template": "g/extra",
            "template_position": 2,
            "probabilities": {"g/extra#2": 0.7},
            "error": None,
        },
        ("D", "s6"): {
            "answer": NONE,
            "probability": 0.8,
            "template": None,
            "template_position": None,
            "probabilities": {"none": 0.8},
            "error": None,
        },
    }
    changes = {c["section_id"]: c for c in refining.refine(ROWS, PARENTS, DOCS, extras)}
    assert set(changes) == {"s0", "s1", "s2", "s3", "s4", "s5", "s6"}  # every section
    got = {
        k: (c["method"], c["template"], c["template_position"], c["refined_doubtful"])
        for k, c in changes.items()
    }
    assert got == {
        "s0": ("model", T, 1, True),  # the model's own, only 40%, and no extra fitted
        "s1": ("parent", T, 1, True),  # its parent's answer was only 40%
        "s2": ("parent", T, 1, True),  # through s1
        "s3": ("extra", "g/extra", 2, False),
        "s4": ("parent", "g/extra", 2, False),  # goes with s3's extra section
        "s5": ("nearest", T, 2, True),  # unsure of "none": its likeliest other option
        "s6": ("other", "g/extra", OTHER, True),  # sure of "none", and no extra fitted
    }
    assert changes["s6"]["extra_answer"] == NONE  # the extra answer is kept even when unused
    assert changes["s5"]["method_probability"] == 0.3


def test_refining_again_starts_from_the_models_answers():
    first = refining.refine(ROWS, PARENTS, DOCS, {})
    assert refining.refine(ROWS, PARENTS, DOCS, {}) == first
    assert refining.nearest(choice("x", answer=NONE, none=0.5, ps={"none": 0.5})) is None


def row(section, template=None, position=None, p=0.9, none=0.05, ps=None, heading="", chars=None):
    r = choice(section, template, position, "A" if template else NONE, p, none, ps)
    r |= {"model_template": template, "model_template_position": position, "heading": heading}
    if chars is not None:
        r["own_chars"] = chars
    return r


def refined(rows, parents, extras=None, docs=DOCS):
    return {
        c["section_id"]: c
        for c in refining.refine(
            rows, {("D", s): p for s, p in parents.items()}, docs, extras or {}
        )
    }


def test_torn_between_two_template_sections_is_both():
    torn = row("a", T, 1, p=0.45, none=0.15, ps={f"{T}#1": 0.45, f"{T}#3": 0.4, "none": 0.15})
    unsure = row("b", T, 1, p=0.45, none=0.4, ps={f"{T}#1": 0.45, f"{T}#3": 0.15, "none": 0.4})
    got = refined([torn, unsure], {"a": None, "b": None})
    assert (got["a"]["method"], got["a"]["also_template"], got["a"]["also_position"]) == (
        "combined",
        T,
        3,
    )
    assert got["a"]["method_probability"] == pytest.approx(0.85)
    assert not got["a"]["refined_doubtful"]
    assert (got["b"]["method"], got["b"]["refined_doubtful"]) == ("model", True)
    # only the one that isn't a combination is asked the extra question
    assert refining.to_ask([torn, unsure], {}, DOCS) == [("D", "b")]


def test_a_doubtful_match_can_become_an_extra_section():
    r = row("a", T, 1, p=0.3, none=0.45, ps={f"{T}#1": 0.3, f"{T}#2": 0.25, "none": 0.45})
    sure = {"template": X, "template_position": 3, "probability": 0.7, "answer": "C"}
    unsure = {**sure, "probability": 0.25}
    assert refined([r], {"a": None}, {("D", "a"): sure})["a"]["method"] == "extra"
    kept = refined([r], {"a": None}, {("D", "a"): unsure})["a"]
    assert (kept["method"], kept["template"], kept["refined_doubtful"]) == ("model", T, True)


def test_a_heading_with_no_text_takes_its_subsections_mapping():
    rows = [
        row("part", none=0.9, heading="Section two", chars=10),
        row("c1", T, 2, chars=1000),
        row("c2", T, 2, p=0.8, chars=500),
        row("c3", T, 4, chars=100),
        row("mixed", none=0.9, heading="Part 4", chars=0),
        row("m1", T, 2, chars=500),
        row("m2", T, 4, chars=500),
        row("m3", T, 5, chars=500),
        row("long", none=0.9, heading="Background", chars=2000),
        row("l1", T, 2, chars=100),
    ]
    parents = {
        "part": None,
        "c1": "part",
        "c2": "part",
        "c3": "part",
        "mixed": None,
        "m1": "mixed",
        "m2": "mixed",
        "m3": "mixed",
        "long": None,
        "l1": "long",
    }
    got = refined(rows, parents)
    assert (got["part"]["method"], got["part"]["template_position"]) == ("subsections", 2)
    assert got["part"]["method_probability"] == pytest.approx(1502 / 1603, abs=1e-3)
    assert not got["part"]["refined_doubtful"]
    # no mapping has most of its subsections' text: it only names a part of the report
    assert (got["mixed"]["method"], got["mixed"]["template"]) == ("subsections", X)
    assert got["mixed"]["template_position"] == REPORT_PART
    # it has text of its own, so it is left to the other rules: nothing else fits
    assert (got["long"]["method"], got["long"]["template_position"]) == ("other", OTHER)


def test_a_subsection_that_goes_with_its_parent_follows_the_parents_final_mapping():
    rows = [
        row("part", none=0.9, heading="Section two", chars=0),
        row("c1", none=0.9, chars=300),
        row("c2", T, 3, chars=900),
    ]
    got = refined(rows, {"part": None, "c1": "part", "c2": "part"})
    assert (got["part"]["method"], got["part"]["template_position"]) == ("subsections", 3)
    assert (got["c1"]["method"], got["c1"]["template_position"]) == ("parent", 3)


def test_appendices_go_to_appendices_and_keep_what_they_are_about():
    rows = [
        row("intro", T, 1),
        row("app", T, 5, heading="Appendix A: Terms of reference"),
        row("panel", T, 6, heading="Panel members"),
        row("annex", none=0.95, heading="ANNEX 2", chars=500),
    ]
    got = refined(rows, {"intro": None, "app": None, "panel": "app", "annex": None})
    assert got["intro"]["method"] == "model"
    for s, about in (("app", (T, 5)), ("panel", (T, 6)), ("annex", None)):
        c = got[s]
        assert (c["method"], c["template"], c["template_position"]) == ("appendix", X, APPENDICES)
        assert ((c["also_template"], c["also_position"]) if c["also_template"] else None) == about
        assert not c["refined_doubtful"]


def test_extra_sections_are_placed_where_reviews_most_often_have_them():
    def mapped(doc, *mappings):
        return [
            {"doc_id": doc, "section_id": str(i), "template": t, "template_position": p}
            for i, (t, p) in enumerate(mappings)
        ]

    changes = (
        mapped("D1", (T, 1), (X, 3), (T, 2), (X, 8))
        + mapped("D2", (T, 1), (T, 1), (X, 3), (X, 3), (T, 3))
        + mapped("D3", (X, 3), (T, 1), (X, 1))
    )
    docs = {d: {"doc_id": d, "template": T} for d in ("D1", "D2", "D3")}
    places = {p["extra_position"]: p for p in refining.placements(changes, docs, X, {8})}
    assert set(places) == {1, 3}  # Appendices (8) is left to the view: always last
    assert (places[3]["after_position"], places[3]["n_reviews"], places[3]["n_seen"]) == (1, 2, 3)
    assert places[1]["after_position"] == 1 and places[1]["n_seen"] == 1


def test_stored_extra_answers_are_read_back():
    got = refining.stored_extra("B", 0.7, json.dumps({"g/extra#2": 0.7, "none": 0.3}), None)
    assert (got["template"], got["template_position"], got["probability"]) == ("g/extra", 2, 0.7)
    assert refining.stored_extra("Z", 0.8, {"none": 0.8}, None)["template"] is None
    assert refining.stored_extra(None, None, None, None) is None


def test_extra_question_offers_only_the_sections_the_model_can_choose():
    asked = refining.asked_extras(refining.extra_template("dardr-2026"))
    headings = [s.heading for s in asked.sections]
    assert "Appendices" not in headings and "Other content" not in headings
    assert headings[-1] == "References and correspondence"
    assert [s.position for s in asked.sections] == list(range(1, 10))  # positions unchanged


def test_unsure_reviews_get_the_template_their_sections_fit_best():
    rows = [
        {**row("s1", none=0.9, chars=1000), "answer": NONE},
        {**row("s2", "g/a", 1, p=0.6, none=0.3, chars=100)},
    ]

    def result(section, none):
        return {
            "meta": {
                "doc_id": "D",
                "section_id": section,
                "options": {"A": ["g/b", 1]},
                "template": "g/b",
            },
            "answer": "A" if none < 0.5 else NONE,
            "probabilities": {"A": 1 - none, NONE: none},
        }

    decisions = stage.choose_by_sections(
        [result("s1", 0.1), result("s2", 0.2)], rows, {"D": "g/a"}, ["g/a", "g/b"]
    )
    d = decisions["D"]
    assert d["template"] == "g/b" and d["previous"] == "g/a"
    assert d["scores"]["g/a"]["fit"] == pytest.approx((1100 * 0.1 + 200 * 0.7) / 1300, abs=1e-3)
    assert d["scores"]["g/b"]["fit"] == pytest.approx((1100 * 0.9 + 200 * 0.8) / 1300, abs=1e-3)
    assert d["scores"]["g/b"]["filled"] == 1 and d["filled"] == 1
    assert set(d["answers"]) == {"s1", "s2"}
    # a review that matched no template at all is asked against every template
    none_yet = stage.choose_by_sections([result("s1", 0.1)], rows[:1], {"D": None}, ["g/b"])
    assert none_yet["D"]["template"] == "g/b" and none_yet["D"]["previous"] is None


def test_a_template_must_fit_the_reviews_structure_not_just_take_its_text():
    """A small template with broad sections (the 2026 IMR template) can take most of a full
    report's text, but a full template fills many more of its own sections."""
    sections = [f"s{i}" for i in range(6)]
    rows = [{**row(s, "g/full", i + 1, p=0.6, none=0.3, chars=500)} for i, s in enumerate(sections)]

    def small(section, i):  # every section fits one of the small template's 2 sections
        return {
            "meta": {
                "doc_id": "D",
                "section_id": section,
                "options": {"A": ["g/small", 1], "B": ["g/small", 2]},
                "template": "g/small",
            },
            "answer": "AB"[i % 2],
            "probabilities": {"AB"[i % 2]: 0.95, NONE: 0.05},
        }

    d = stage.choose_by_sections(
        [small(s, i) for i, s in enumerate(sections)], rows, {"D": "g/full"}, ["g/full", "g/small"]
    )["D"]
    assert d["scores"]["g/small"]["fit"] > d["scores"]["g/full"]["fit"]  # 0.95 against 0.7
    assert (d["scores"]["g/full"]["filled"], d["scores"]["g/small"]["filled"]) == (6, 2)
    assert d["template"] == "g/full"  # 0.7 x 6/6 against 0.95 x 2/6
    assert d["scores"]["g/small"]["score"] == pytest.approx(0.95 * 2 / 6, abs=1e-3)


# ---------------------------------------------------------------- the database (optional)


@pytest.fixture
def database():
    url = os.environ.get("DHR_TEST_DATABASE_URL")
    if not url:
        pytest.skip("set DHR_TEST_DATABASE_URL to an empty test database to run this")
    from f.dhr.db import apply_migrations, connect

    conn = connect(url)
    for schema in ("corpus", "evaluation", "scratch"):
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    apply_migrations(conn)
    yield conn
    conn.close()


def test_choices_tables_and_views(database):
    conn = database
    rev, g = "test-revision-choices", "test-guidance"
    conn.execute("DELETE FROM corpus.dataset_versions WHERE revision = %s", (rev,))
    conn.execute("DELETE FROM corpus.guidance_documents WHERE guidance = %s", (g,))
    conn.execute(
        "INSERT INTO corpus.dataset_versions (revision, repo_id, n_documents, dataset)"
        " VALUES (%s, 'x', 2, 'TEST')",
        (rev,),
    )
    conn.execute(
        "INSERT INTO corpus.grouped_versions (revision, headings, n_documents, n_sections, n_blocks,"
        " n_skipped) VALUES (%s, 'checked', 2, 8, 8, 0)",
        (rev,),
    )
    headings = ["Timescales", "Timeline", "Timeline analysis", "Glossary"]
    for doc in ("D", "E"):
        conn.execute(
            "INSERT INTO corpus.documents (revision, doc_id, file_path, sha256, n_pages, n_blocks,"
            " raw) VALUES (%s, %s, 'f.json', 'x', 1, 1, '{}')",
            (rev, doc),
        )
        for i, heading in enumerate(headings):
            conn.execute(
                "INSERT INTO corpus.sections (revision, doc_id, section_id, parent_id, level,"
                " heading, position, last_position, first_page, last_page, n_blocks)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 0, 0, 0)",
                (rev, doc, f"s{i}", "s1" if i == 2 else None, 2 if i == 2 else 1, heading, i, i),
            )
    conn.execute(
        "INSERT INTO corpus.guidance_documents (guidance, title, source_url, sha256, n_pages)"
        " VALUES (%s, 'Test guidance', 'u', 'x', 1)",
        (g,),
    )
    for name, appendix, kind, sections in [
        (f"{g}/overview", 1, "overview report", ["Timescales", "Timeline", "Conclusions"]),
        (f"{g}/plan", 2, "action plan", ["Recommendation"]),
    ]:
        conn.execute(
            "INSERT INTO corpus.templates (template, guidance, appendix, title, kind, first_page,"
            " last_page) VALUES (%s, %s, %s, %s, %s, 1, 2)",
            (name, g, appendix, name.split("/")[1].title(), kind),
        )
        for position, heading in enumerate(sections, start=1):
            conn.execute(
                "INSERT INTO corpus.template_sections (template, position, heading, printed,"
                " guidance, page) VALUES (%s, %s, %s, true, '', 1)",
                (name, position, heading),
            )
    documents = [
        {
            "doc_id": "D",
            "template": f"{g}/overview",
            "answer": "A",
            "probability": 0.9,
            "probabilities": {f"{g}/overview": 0.9},
            "error": None,
        },
        {
            "doc_id": "E",
            "template": None,
            "answer": NONE,
            "probability": 0.8,
            "probabilities": {"none": 0.8},
            "error": None,
        },
    ]

    def row(doc, section, template=None, position=None, answer=None, p=None, error=None):
        return {
            "doc_id": doc,
            "section_id": section,
            "template": template,
            "template_position": position,
            "answer": answer,
            "probability": p,
            "none_probability": 0.0,
            "probabilities": {},
            "n_options": 4,
            "error": error,
        }

    rows = [
        row("D", "s0", f"{g}/overview", 1, "A", 0.9),
        row("D", "s1", f"{g}/overview", 2, "B", 0.8),
        row("D", "s2", f"{g}/overview", 2, "B", 0.4),  # shares the template's timeline
        row("D", "s3", answer=NONE, p=0.7),
        *[row("E", f"s{i}", error=stage.NOT_ASKED) for i in range(4)],
    ]
    version = {
        "revision": rev,
        "guidance": g,
        "reviews": "pilot",
        "model": "m",
        "model_revision": "abc",
        "prompt_version": PROMPT_VERSION,
        "prompts_sha256": json.dumps({"r-documents": "a", "r-sections": "b"}),
        "run_details": "{}",
        "slurm_job_ids": ["1", "2"],
        "blythe_runs": ["r-documents", "r-sections"],
        "git_commit": "local",
        **stage.counts(documents, rows),
    }
    assert version["n_unmatched"] == 5 and version["n_no_answer"] == 0
    stage.store(conn, version, documents, rows)
    stage.store(conn, version, documents, rows)  # choosing again replaces the choices

    statuses = conn.execute(
        "SELECT doc_id, section_id, status, template_heading FROM corpus.current_template_choices"
        " WHERE revision = %s ORDER BY doc_id, section_position",
        (rev,),
    ).fetchall()
    assert statuses[:4] == [
        ("D", "s0", "matched", "Timescales"),
        ("D", "s1", "matched", "Timeline"),
        ("D", "s2", "matched", "Timeline"),
        ("D", "s3", "unmatched", None),
    ]
    assert {s for _, _, s, _ in statuses[4:]} == {"unmatched"}  # E matched no template
    assert conn.execute(
        "SELECT doc_id, status FROM corpus.current_document_choices WHERE revision = %s"
        " ORDER BY doc_id",
        (rev,),
    ).fetchall() == [("D", "matched"), ("E", "unmatched")]

    doubtful = conn.execute(
        "SELECT section_id FROM corpus.current_template_choices WHERE revision = %s"
        " AND doubtful ORDER BY doc_id, section_position",
        (rev,),
    ).fetchall()
    assert doubtful == [("s2",)]  # the 40% answer; unmatched sections are never "doubtful"

    parts = conn.execute(
        "SELECT doc_id, status, template_heading, section_ids FROM corpus.current_standardised"
        " WHERE revision = %s ORDER BY doc_id, part",
        (rev,),
    ).fetchall()
    assert parts == [
        ("D", "template section", "Timescales", ["s0"]),
        ("D", "template section", "Timeline", ["s1", "s2"]),  # both, in the review's order
        ("D", "template section", "Conclusions", []),  # nothing in the review
        ("D", "template section", "Recommendation", []),  # the action plan's
        ("D", "unmatched", None, ["s3"]),
        ("E", "unmatched", None, ["s0", "s1", "s2", "s3"]),
    ]

    # refining: D's s3 ("Glossary", said none at 70%) is asked the extra question
    extras = refining.store_extra_template(conn, g)
    refining.store_extra_template(conn, g)  # again: it only updates
    rows_, parents_, documents_ = refining.read_choices(conn, rev, g)
    # s2 (40%) is a doubtful match, so it is asked too
    assert refining.to_ask(rows_, parents_, documents_) == [("D", "s2"), ("D", "s3")]
    answer = {
        "answer": "C",
        "probability": 0.9,
        "template": extras.template,
        "template_position": 3,
        "probabilities": {f"{extras.template}#3": 0.9},
        "error": None,
    }
    # D's title page: text before its first heading
    conn.execute(
        "INSERT INTO corpus.blocks (revision, doc_id, block_id, position, page, block_type,"
        " section_id, html, text) VALUES (%s, 'D', 'b0', 0, 0, 'Text', NULL, '', 'A title')",
        (rev,),
    )
    x = {"rows": rows_, "parents": parents_, "documents": documents_}
    changes, places = stage.apply_rules(x, g, {("D", "s3"): answer})
    assert [(p["extra_position"], p["after_position"]) for p in places] == [(3, 2)]
    counts = refining.store_refinement(conn, rev, g, changes, {"test": True}, places)
    assert counts["n_extra"] == 1 and counts["n_unmatched"] == 4  # E's four
    assert counts["n_doubtful"] == 1  # s2, still only 40%
    assert conn.execute(
        "SELECT method, template_heading, mapped_probability, doubtful, answer"
        " FROM corpus.current_template_choices WHERE revision = %s AND section_id = 's3'"
        " AND doc_id = 'D'",
        (rev,),
    ).fetchone() == ("extra", "Glossary and definitions", pytest.approx(0.9), False, NONE)
    parts = conn.execute(
        "SELECT template_heading, section_ids FROM corpus.current_standardised"
        " WHERE revision = %s AND doc_id = 'D' ORDER BY part",
        (rev,),
    ).fetchall()
    # the glossary where reviews have it (after the timeline), the title-page text first
    assert parts == [
        ("Front matter", []),
        ("Timescales", ["s0"]),
        ("Timeline", ["s1", "s2"]),
        ("Glossary and definitions", ["s3"]),
        ("Conclusions", []),
        ("Recommendation", []),
    ]
    assert conn.execute(
        "SELECT template_heading, n_opening_blocks FROM corpus.current_standardised"
        " WHERE revision = %s AND doc_id = 'D' AND n_opening_blocks > 0",
        (rev,),
    ).fetchall() == [("Front matter", 1)]
    # refining again from scratch, with no extra answer, undoes it
    rows_, parents_, documents_ = refining.read_choices(conn, rev, g)
    assert [
        r["model_template"] for r in rows_ if r["section_id"] == "s3" and r["doc_id"] == "D"
    ] == [None]
    refining.store_refinement(conn, rev, g, refining.refine(rows_, parents_, documents_, {}), {})
    assert conn.execute(
        "SELECT method, template_heading, doubtful FROM corpus.current_template_choices"
        " WHERE revision = %s AND section_id = 's3' AND doc_id = 'D'",
        (rev,),
    ).fetchone() == ("other", "Other content", True)  # nothing fits it: the catch-all
    # the stored extra answer is read back, so the rules can run again without asking
    assert [r["stored_extra"] for r in refining.read_choices(conn, rev, g)[0]][3] is None

    # a review the model wasn't sure of gets the template its sections fit best
    conn.execute(
        "UPDATE corpus.document_choices SET fallback = true WHERE revision = %s AND doc_id = 'D'",
        (rev,),
    )
    assert stage.unsure_reviews(conn, rev, g) == {"D": f"{g}/overview", "E": None}
    assert conn.execute(
        "SELECT bool_and(review_doubtful), bool_or(doubtful) FROM corpus.current_template_choices"
        " WHERE revision = %s AND doc_id = 'D' AND section_id IN ('s0', 's1')",
        (rev,),
    ).fetchone() == (True, False)  # the review is doubtful, not its sure sections
    decisions = {
        "D": {
            "doc_id": "D",
            "previous": f"{g}/overview",
            "template": f"{g}/overview",
            "fit": 0.9,
            "filled": 3,
            "margin": 0.9,
            "scores": {f"{g}/overview": {"fit": 0.9, "filled": 3, "score": 0.9}},
            "answers": None,
            "section_ids": ["s0", "s1", "s2", "s3"],
        },
        "E": {
            "doc_id": "E",
            "previous": None,
            "template": f"{g}/overview",
            "fit": 0.5,
            "filled": 1,
            "margin": 0.05,
            "scores": {f"{g}/overview": {"fit": 0.5, "filled": 1, "score": 0.5}},
            "answers": {
                "s0": {
                    **row("E", "s0", f"{g}/overview", 3, "C", 0.7),
                    "none_probability": 0.1,
                    "probabilities": {f"{g}/overview#3": 0.7},
                }
            },
            "section_ids": ["s0", "s1", "s2", "s3"],
        },
    }
    asked = {
        "meta": {
            "doc_id": "E",
            "section_id": "s0",
            "options": {"A": [f"{g}/overview", 3]},
            "template": f"{g}/overview",
        },
        "answer": "A",
        "probabilities": {"A": 0.7, "Z": 0.1},
    }
    stage.store_retemplate(conn, rev, g, decisions, [asked], rows_, {"D": f"{g}/overview"})
    assert conn.execute(
        "SELECT doc_id, template, method, fallback, doubtful FROM corpus.current_document_choices"
        " WHERE revision = %s ORDER BY doc_id",
        (rev,),
    ).fetchall() == [
        ("D", f"{g}/overview", "sections", False, False),
        ("E", f"{g}/overview", "sections", False, True),  # only 0.05 ahead of the next
    ]
    assert conn.execute(
        "SELECT section_id, status, template_heading, model_template_position, error"
        " FROM corpus.current_template_choices WHERE revision = %s AND doc_id = 'E'"
        " ORDER BY section_position",
        (rev,),
    ).fetchall() == [
        ("s0", "matched", "Conclusions", 3, None),
        ("s1", "no answer", None, None, "no result: the job ended before answering it"),
        ("s2", "no answer", None, None, "no result: the job ended before answering it"),
        ("s3", "no answer", None, None, "no result: the job ended before answering it"),
    ]
    # every answer is kept, so a review is never asked the same thing twice
    kept = stage.stored_retemplate_answers(conn, rev, g)
    assert kept[("E", f"{g}/overview")]["s0"]["template_position"] == 3
    assert len(kept[("D", f"{g}/overview")]) == 4  # D's answers for the template it has
    # reviews given a template by their sections are scored again on the next run
    assert stage.unsure_reviews(conn, rev, g) == {"D": f"{g}/overview", "E": f"{g}/overview"}

    with pytest.raises(Exception):  # a template without its position isn't allowed
        conn.execute(
            "UPDATE corpus.template_choices SET template_position = NULL WHERE section_id = 's1'"
            " AND doc_id = 'D' AND revision = %s",
            (rev,),
        )
    # removing the dataset version removes its choices
    conn.execute("DELETE FROM corpus.dataset_versions WHERE revision = %s", (rev,))
    for table in ("choice_versions", "document_choices", "template_choices"):
        count = f"SELECT count(*) FROM corpus.{table} WHERE revision = %s"
        assert conn.execute(count, (rev,)).fetchone()[0] == 0
    conn.execute("DELETE FROM corpus.guidance_documents WHERE guidance = %s", (g,))


def test_gzip_member_helper_is_standard():
    raw = stage.gzip_lines([{"id": "a"}])
    assert gzip.GzipFile(fileobj=io.BytesIO(raw)).read() == b'{"id": "a"}\n'
