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
    }
    with pytest.raises(ValueError):
        stage.choice_rows([section_answer("A")], sections[1:], documents)  # not asked for


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
