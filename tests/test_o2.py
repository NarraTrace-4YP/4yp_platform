"""O2: review Markdown, the WeKnora client and saving a wiki (no WeKnora or Blythe needed)."""

import json

import httpx
import pytest

from f.dhr.O2 import reviews, wiki_store
from f.dhr.O2.instructions import CONTENT, EXTRACTION
from f.dhr.weknora import WeKnora, WeKnoraError


def block(position, page, kind, text, section="/s/1", html=""):
    return {
        "block_id": f"/page/{page}/{kind}/{position}",
        "position": position,
        "page": page,
        "block_type": kind,
        "section_id": section,
        "html": html,
        "text": text,
    }


def test_a_paragraph_split_by_a_page_break_is_joined_and_its_footnote_follows():
    blocks = [
        block(0, 0, "SectionHeader", "Background", section="/s/1"),
        block(1, 0, "Text", "Mary told her GP about the"),
        block(2, 0, "Footnote", "1 General practitioner."),
        block(3, 1, "Text", "abuse in March 2017.1"),
        block(4, 1, "Text", "A new paragraph."),
    ]
    text, joins = reviews.render_markdown("Mary - Bromley", blocks, {"/s/1": 1})
    assert joins == 1
    assert (
        "Mary told her GP about the abuse in March 2017.1\n\nFootnote: 1 General practitioner."
        in text
    )
    assert text.index("Footnote") < text.index("A new paragraph.")
    assert text.startswith("# Mary - Bromley\n\n## Background")


def test_complete_sentences_capitals_and_other_sections_are_not_joined():
    blocks = [
        block(0, 0, "Text", "The review ended."),
        block(1, 1, "Text", "and so on"),
        block(2, 1, "Text", "No full stop here"),
        block(3, 2, "Text", "The next page starts a sentence."),
        block(4, 2, "Text", "Different section, cut"),
        block(5, 3, "Text", "off here.", section="/s/2"),
        block(6, 3, "Text", "Same page, no stop"),
        block(7, 3, "Text", "lower case but same page."),
    ]
    _, joins = reviews.join_split_paragraphs(blocks)
    assert joins == 0


def test_a_paragraph_over_three_pages_joins_twice():
    blocks = [block(0, 0, "Text", "one"), block(1, 1, "Text", "two"), block(2, 2, "Text", "three.")]
    out, joins = reviews.join_split_paragraphs(blocks)
    assert joins == 2 and [b["text"] for b in out] == ["one two three."]


def test_headings_keep_their_depth_and_carry_the_template_section():
    blocks = [
        block(0, 0, "SectionHeader", "1. Introduction", section="/a"),
        block(1, 0, "SectionHeader", "1.1 Scope", section="/b"),
        block(2, 0, "TableOfContents", "Contents ... 3", section="/b"),
        block(3, 0, "Table", "x", section="/b", html="<table><tr><td>x</td></tr></table>"),
    ]
    labels = {"document": "Overview report", "sections": {"/b": "Terms of reference"}}
    text, _ = reviews.render_markdown("R", blocks, {"/a": 1, "/b": 2}, labels, "dhr-2016")
    assert "Report template (2016 guidance): Overview report" in text
    assert (
        "## 1. Introduction\n\n### 1.1 Scope\n\nTemplate section (2016 guidance): Terms of reference"
        in text
    )
    assert "Contents" not in text and "<table>" in text


def test_a_template_section_is_not_repeated_for_subsections_mapped_the_same():
    blocks = [
        block(0, 0, "SectionHeader", "2. Chronology", section="/a"),
        block(1, 0, "SectionHeader", "2.1 2017", section="/b"),
        block(2, 0, "SectionHeader", "3. Analysis", section="/c"),
    ]
    labels = {
        "document": None,
        "sections": {"/a": "Chronology", "/b": "Chronology", "/c": "Analysis"},
    }
    text, _ = reviews.render_markdown("R", blocks, {"/a": 1, "/b": 2, "/c": 1}, labels, "dhr-2016")
    assert text.count("Template section (2016 guidance): Chronology") == 1
    assert "Template section (2016 guidance): Analysis" in text


def test_review_choices_are_checked():
    with pytest.raises(ValueError):
        reviews.check_choices("DHRC-200", "pilot", None)
    with pytest.raises(ValueError):
        reviews.check_choices("both", "chosen", [])
    with pytest.raises(ValueError):
        reviews.check_choices("both", "all", None, "dhr-2020")
    assert len(reviews.PILOT_REVIEWS) == 10


def test_the_default_instructions_fit_weknoras_limit():
    assert len(EXTRACTION.encode()) <= 4000 and len(CONTENT.encode()) <= 4000


def fake_weknora(handler, shared=False):
    return WeKnora(
        key="k",
        base_url="http://wk/api/v1",
        shared=shared,
        client=httpx.Client(
            base_url="http://wk/api/v1",
            transport=httpx.MockTransport(handler),
            headers={"X-API-Key": "k"},
        ),
    )


def test_in_the_shared_space_only_the_runners_own_model_entry_is_used():
    models = [
        {
            "id": "m1",
            "name": "Qwen/Qwen3.8-27B-FP8",
            "display_name": "Qwen (rshields2004)",
            "type": "KnowledgeQA",
        },
        {
            "id": "m2",
            "name": "Qwen/Qwen3.8-27B-FP8",
            "display_name": "Qwen (markcoding05)",
            "type": "KnowledgeQA",
        },
    ]
    wk = fake_weknora(
        lambda r: httpx.Response(200, json={"success": True, "data": models}), shared=True
    )
    assert wk.chat_model("Qwen/Qwen3.8-27B-FP8", user="markcoding05")["id"] == "m2"
    assert wk.chat_model("Qwen/Qwen3.8-27B-FP8", user="rshields2004")["id"] == "m1"
    with pytest.raises(WeKnoraError, match="no model entry of yours"):
        wk.chat_model("Qwen/Qwen3.8-27B-FP8", user="someone_else")


def test_the_shared_key_comes_first(monkeypatch):
    from f.dhr import weknora

    values = {"f/dhr/weknora_api_key": "sk-team", "u/me/weknora_api_key": "sk-mine"}
    monkeypatch.delenv("WEKNORA_API_KEY", raising=False)
    monkeypatch.setenv("WM_USERNAME", "me")
    monkeypatch.setattr(weknora, "_variable", lambda path: values.get(path))
    assert weknora.key_source() == ("sk-team", True)
    del values["f/dhr/weknora_api_key"]
    assert weknora.key_source() == ("sk-mine", False)
    del values["u/me/weknora_api_key"]
    with pytest.raises(WeKnoraError, match="No WeKnora API key"):
        weknora.key_source()


def test_the_client_makes_a_wiki_only_knowledge_base_and_finds_the_model():
    seen = {}

    def handler(request):
        assert request.headers["X-API-Key"] == "k"
        if request.url.path.endswith("/models"):
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": [
                        {"id": "e1", "name": "bge", "type": "Embedding"},
                        {"id": "m1", "name": "Qwen/Qwen3.8-27B-FP8", "type": "KnowledgeQA"},
                    ],
                },
            )
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"success": True, "data": {"id": "kb1"}})

    wk = fake_weknora(handler)
    assert wk.chat_model("Qwen/Qwen3.8-27B-FP8")["id"] == "m1"
    with pytest.raises(WeKnoraError, match="no chat model"):
        wk.chat_model("other")
    assert wk.create_knowledge_base(name="n", description="d", chat_model_id="m1")["id"] == "kb1"
    strategy = seen["body"]["indexing_strategy"]
    assert strategy == {
        "vector_enabled": False,
        "keyword_enabled": False,
        "wiki_enabled": True,
        "graph_enabled": False,
    }
    assert seen["body"]["wiki_config"]["synthesis_model_id"] == "m1"


def test_the_client_pages_through_wiki_pages_and_explains_a_bad_key():
    def handler(request):
        if "stats" in request.url.path:
            return httpx.Response(401, json={"error": "unauthorized"})
        page = int(request.url.params["page"])
        return httpx.Response(200, json={"pages": [{"slug": f"p{page}"}], "total_pages": 2})

    wk = fake_weknora(handler)
    assert [p["slug"] for p in wk.wiki_pages("kb")] == ["p1", "p2"]
    with pytest.raises(WeKnoraError, match="refused the API key"):
        wk.wiki_stats("kb")


def test_page_sources_map_back_to_reviews():
    by_knowledge = {"k1": "Mary - Bromley"}
    by_file = {"Penny - Somerset.md": "Penny - Somerset"}
    page = {"source_refs": ["k1|Mary - Bromley.md", "k9|Penny - Somerset", "zz|unknown"]}
    assert wiki_store._source_doc_ids(page, by_knowledge, by_file) == {
        "Mary - Bromley",
        "Penny - Somerset",
    }
    assert wiki_store.new_instance_id().startswith("wiki-")


def test_choosing_rendering_and_saving_against_a_real_database():
    """Needs DHR_TEST_DATABASE_URL (an empty test database): applies every migration there."""
    import os

    url = os.environ.get("DHR_TEST_DATABASE_URL")
    if not url:
        pytest.skip("set DHR_TEST_DATABASE_URL to an empty test database to run this")
    from f.dhr.db import apply_migrations, connect

    with connect(url) as conn:
        for schema in ("corpus", "evaluation", "scratch"):
            conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        apply_migrations(conn)
        conn.execute("DELETE FROM corpus.dataset_versions WHERE revision IN ('r100', 'rraw')")
        conn.execute("DELETE FROM wiki.instances WHERE instance_id = 'w1'")
        for rev, ds, repo in (
            ("r100", "DHRC-100", "4YP/dhr-corpus-100-copy"),
            ("rraw", "DHRC-RAW", "4YP/dhr-corpus-raw-copy"),
        ):
            conn.execute(
                "INSERT INTO corpus.dataset_versions (revision, repo_id, n_documents, dataset)"
                " VALUES (%s, %s, 2, %s)",
                (rev, repo, ds),
            )
            conn.execute(
                "INSERT INTO corpus.grouped_versions (revision, headings, n_documents,"
                " n_sections, n_blocks, n_skipped) VALUES (%s, 'checked', 1, 1, 2, 0)",
                (rev,),
            )
            docs = ["A - X - 2018_a - DHR"] + (["B - Y - 2019_a - DHR"] if ds == "DHRC-RAW" else [])
            for doc in docs:
                conn.execute(
                    "INSERT INTO corpus.documents VALUES (%s, %s, 'f', 's', 2, 2, '{}')", (rev, doc)
                )
                conn.execute(
                    "INSERT INTO corpus.sections VALUES (%s, %s, '/h', NULL, 1, 'Intro', 0, 1, 0, 1, 1)",
                    (rev, doc),
                )
                conn.execute(
                    "INSERT INTO corpus.blocks VALUES (%s, %s, '/h', 0, 0, 'SectionHeader', '/h', '', 'Intro')",
                    (rev, doc),
                )
                conn.execute(
                    "INSERT INTO corpus.blocks VALUES (%s, %s, '/t', 1, 1, 'Text', '/h', '', %s)",
                    (rev, doc, f"{ds} text."),
                )
        both = reviews.choose_reviews(conn, "both", "all")
        assert [(r["doc_id"][0], r["dataset"]) for r in both] == [
            ("A", "DHRC-100"),
            ("B", "DHRC-RAW"),
        ]
        with pytest.raises(ValueError, match="aren't in"):
            reviews.choose_reviews(conn, "DHRC-100", "pilot")
        f = reviews.review_file(conn, both[0], "dhr-2016")
        assert f["text"] == "# A - X - 2018_a - DHR\n\n## Intro\n\nDHRC-100 text.\n"
        assert f["n_template_labels"] == 0

        conn.execute(
            "INSERT INTO wiki.instances (instance_id, name, created_by, dataset, reviews, model,"
            " settings) VALUES ('w1', 'n', 'me', 'both', 'all', 'm', '{}')"
        )
        conn.execute(
            "INSERT INTO wiki.documents (instance_id, doc_id, dataset, revision, file_name, sha256,"
            " n_chars, knowledge_id) VALUES ('w1', 'A - X - 2018_a - DHR', 'DHRC-100', 'r100',"
            " 'A - X - 2018_a - DHR.md', 'h', 10, 'k1')"
        )
        pages = [
            {
                "slug": "entity/a",
                "title": "A",
                "page_type": "entity",
                "content": "x",
                "out_links": ["concept/c", "entity/a", "missing/page"],
                "source_refs": ["k1|A - X - 2018_a - DHR.md"],
            },
            {
                "slug": "concept/c",
                "title": "C",
                "page_type": "concept",
                "content": "y",
                "out_links": [],
            },
        ]
        assert wiki_store.save_wiki(conn, "w1", pages, {"total_pages": 2}) == {
            "pages": 2,
            "links": 2,
            "broken_links": 1,
            "page_sources": 1,
        }
        assert wiki_store.save_wiki(conn, "w1", pages[1:], {})["pages"] == 1  # replaces the copy
        assert (
            conn.execute("SELECT count(*) FROM wiki.links WHERE instance_id = 'w1'").fetchone()[0]
            == 0
        )
        wiki_store.set_status(conn, "w1", "built", finished=True)
        assert wiki_store.get_instance(conn, "w1")["status"] == "built"


def test_the_app_backends_read_a_saved_wiki():
    import os

    url = os.environ.get("DHR_TEST_DATABASE_URL")
    if not url:
        pytest.skip("set DHR_TEST_DATABASE_URL to an empty test database to run this")
    os.environ["DHR_DATABASE_URL"] = url
    from f.dhr.db import apply_migrations, connect
    from f.dhr.O2.app_backend import get_graph, get_page, get_wikis

    with connect(url) as conn:
        for schema in ("corpus", "evaluation", "scratch"):
            conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        apply_migrations(conn)
        conn.execute("DELETE FROM wiki.instances WHERE instance_id = 'app1'")
        conn.execute(
            "INSERT INTO wiki.instances (instance_id, name, created_by, dataset, reviews, model,"
            " settings) VALUES ('app1', 'n', 'me', 'DHRC-100', 'pilot', 'm', '{}')"
        )
        for part, status in ((1, "completed"), (2, "processing")):
            conn.execute(
                "INSERT INTO wiki.documents (instance_id, doc_id, part, n_parts, dataset, revision,"
                " file_name, sha256, n_chars, knowledge_id, parse_status)"
                " VALUES ('app1', 'R1', %s, 2, 'DHRC-100', 'r', %s, 'h', 5, %s, %s)",
                (part, f"R1 (part {part} of 2).md", f"k{part}", status),
            )
        wiki_store.save_wiki(
            conn,
            "app1",
            [
                {
                    "slug": "a",
                    "title": "A",
                    "page_type": "entity",
                    "content": "see [[b|B]]",
                    "out_links": ["b", "gone"],
                    "source_refs": ["k1|R1.md"],
                },
                {"slug": "b", "title": "B", "page_type": "concept", "content": "y"},
            ],
            {},
        )
    build = next(b for b in get_wikis.main()["builds"] if b["instance_id"] == "app1")
    assert build["n_pages"] == 2 and build["pages_by_type"] == {"entity": 1, "concept": 1}
    assert build["n_reviews"] == 1 and build["n_files"] == 2
    review = build["documents"][0]
    assert review["n_pages"] == 1 and review["n_parts"] == 2 and review["parts_read"] == 1
    assert review["n_chars"] == 10 and review["parse_status"] == "processing"
    graph = get_graph.main("app1")
    assert graph["links"] == [{"source": "a", "target": "b"}] and graph["broken_links"] == 1
    page = get_page.main("app1", "a")
    assert page["sources"] == ["R1"] and [x["slug"] for x in page["links_out"]] == ["b", "gone"]
    assert get_page.main("app1", "b")["links_in"][0]["slug"] == "a"
    assert get_page.main("app1", "nope") is None


def test_progress_moves_from_reading_to_writing():
    from f.dhr.O2 import progress

    reading = progress.build_progress(
        {"completed": 3, "processing": 7}, {"pending_tasks": 0}, 0, 0, None
    )
    assert reading["reviews_done"] == 3 and reading["percent"] < 30
    writing = progress.build_progress(
        {"completed": 10}, {"pending_tasks": 20, "is_active": True}, 50, 80, "model: x", 40
    )
    assert writing["max_pending"] == 40 and 55 < writing["percent"] < 99
    nearly = progress.build_progress(
        {"completed": 9, "failed": 1}, {"pending_tasks": 0}, 90, 200, None, 40
    )
    assert nearly["percent"] == 99 and "(1 failed)" in nearly["line"]
    assert "0 tasks queued, idle" in nearly["line"]


def test_the_gateway_line_reads_its_status():
    from f.dhr.llm import describe_gateway

    assert describe_gateway({"error": "can't"}) == "model: can't"
    assert describe_gateway({"user": "me", "run_id": None}) == "model: no job running yet"
    line = describe_gateway(
        {
            "run_id": "gw-1",
            "slurm_job_id": "123",
            "job_state": "running",
            "calls_with_the_job": 4,
            "calls_waiting_to_send": 1,
            "usage": {"calls": 1234, "completion_tokens": 56789},
        }
    )
    assert line == (
        "model: running (SLURM 123), 4 answering, 1 queued, 1,234 calls / 56,789"
        " tokens written so far"
    )


# --- Parts and knowledge-base settings (25 Nov 2026) ---------------------------------------


def long_review(n_sections=12, paragraphs=6, words=60):
    blocks, levels, position = [], {}, 0
    for s in range(n_sections):
        sid = f"/s/{s}"
        levels[sid] = 1
        blocks.append(block(position, s, "SectionHeader", f"Section {s}", section=sid))
        position += 1
        for p in range(paragraphs):
            blocks.append(
                block(position, s, "Text", " ".join([f"s{s}p{p}"] * words) + ".", section=sid)
            )
            position += 1
    return blocks, levels


def test_a_long_review_is_cut_into_parts_at_headings_and_nothing_is_lost():
    from f.dhr.O2.kb_settings import WEKNORA_WIKI_READ_LIMIT

    blocks, levels = long_review()
    labels = {"document": "DHR", "sections": {"/s/0": "Introduction", "/s/5": "Chronology"}}
    header, pieces, _ = reviews.render_pieces("R", blocks, levels, labels, "dhr-2016")
    whole, _ = reviews.render_markdown("R", blocks, levels, labels, "dhr-2016")
    parts = reviews.split_into_parts("R", header, pieces, 6_000, "dhr-2016")
    assert len(parts) > 3
    assert all(len(p) <= 6_000 < WEKNORA_WIKI_READ_LIMIT for p in parts)
    for i, p in enumerate(parts, start=1):
        assert p.startswith(
            f"# R (part {i} of {len(parts)})\n\nReport template (2016 guidance): DHR"
        )
    # Every paragraph is in exactly one part, in order.
    body = [x.strip() for x in whole.split("\n\n") if x.startswith("s")]
    seen = [x.strip() for p in parts for x in p.split("\n\n") if x.startswith("s")]
    assert seen == body
    # Parts start at a heading wherever one fits.
    assert sum(p.split("\n\n")[2].startswith("## Section") for p in parts[1:]) >= len(parts) - 2


def test_a_part_starting_mid_section_says_where_it_is():
    blocks, levels = long_review(n_sections=1, paragraphs=40)
    labels = {"document": None, "sections": {"/s/0": "Chronology"}}
    header, pieces, _ = reviews.render_pieces("R", blocks, levels, labels, "dhr-2016")
    parts = reviews.split_into_parts("R", header, pieces, 4_000, "dhr-2016")
    assert len(parts) > 2
    assert "Continues the section: Section 0" in parts[1]
    assert "Template section (2016 guidance): Chronology" in parts[1]


def test_a_short_review_is_one_file_with_its_usual_name():
    blocks, levels = long_review(n_sections=1, paragraphs=1, words=5)
    header, pieces, _ = reviews.render_pieces("R", blocks, levels)
    assert reviews.split_into_parts("R", header, pieces, 30_000) == [
        reviews.render_markdown("R", blocks, levels)[0]
    ]


def test_a_piece_bigger_than_a_part_is_cut_at_line_ends():
    table = "<table>" + "\n".join(f"<tr><td>{'x' * 80}</td></tr>" for _ in range(200)) + "</table>"
    pieces = [{"text": table, "depth": 0, "heading": None, "label": None}]
    parts = reviews.split_into_parts("R", [], pieces, 5_000)
    assert len(parts) > 3 and all(len(p) <= 5_000 for p in parts)


def test_knowledge_base_settings_are_checked():
    from f.dhr.O2 import kb_settings as ks

    s = ks.check_settings()
    assert s["part_chars"] == 30_000 and ks.chunking_config(s) == {
        "strategy": "auto",
        "chunk_size": 512,
        "chunk_overlap": 80,
    }
    assert ks.wiki_tuning(s) == {}
    s = ks.check_settings(0, "heading", 2000, 200, ["\n## ", "\n\n"], 10, 4, 4, 2)
    assert ks.chunking_config(s)["separators"] == ["\n## ", "\n\n"]
    assert ks.wiki_tuning(s) == {
        "ingest_batch_size": 10,
        "ingest_map_parallel": 4,
        "ingest_reduce_parallel": 4,
        "ingest_max_inflight": 2,
    }
    for bad, match in [
        ({"part_chars": 40_000}, "part_chars"),
        ({"part_chars": 500}, "part_chars"),
        ({"chunk_strategy": "semantic"}, "chunk_strategy"),
        ({"chunk_size": 50}, "chunk_size"),
        ({"chunk_overlap": 300}, "half"),
        ({"chunk_separators": [""]}, "separators"),
        ({"ingest_map_parallel": 100}, "ingest_map_parallel"),
        ({"chunk_size": True}, "whole number"),
    ]:
        with pytest.raises(ValueError, match=match):
            ks.check_settings(**bad)


def test_the_client_sends_chunking_and_tuning_settings():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"success": True, "data": {"id": "kb1"}})

    wk = fake_weknora(handler)
    wk.create_knowledge_base(
        name="n",
        description="d",
        chat_model_id="m1",
        chunking_config={"strategy": "heading", "chunk_size": 1000, "chunk_overlap": 100},
        wiki_tuning={"ingest_map_parallel": 4},
    )
    assert seen["body"]["chunking_config"]["strategy"] == "heading"
    assert seen["body"]["wiki_config"]["ingest_map_parallel"] == 4
    wk.create_knowledge_base(name="n", description="d", chat_model_id="m1")
    assert "chunking_config" not in seen["body"]


def test_the_graph_example_passes_weknoras_own_checks():
    """Mirrors validateExtractConfig in WeKnora's internal/handler/knowledgebase.go."""
    from f.dhr.O2 import graph

    cfg = graph.extract_config()
    assert cfg["enabled"] and cfg["text"] and cfg["tags"] and all(cfg["tags"])
    names = [n["name"] for n in cfg["nodes"]]
    assert len(names) == len(set(names)) and all(names)
    assert cfg["relations"]
    for r in cfg["relations"]:
        assert r["node1"] in names and r["node2"] in names
        assert r["type"] in cfg["tags"]  # the example only uses the allowed types
    assert len(cfg["custom_instructions"].encode()) <= 4000
    with pytest.raises(ValueError, match="4,000"):
        graph.extract_config("x" * 4001)


def test_the_graph_is_on_by_default_and_can_be_turned_off():
    from f.dhr.O2 import kb_settings as ks

    on = ks.check_settings()
    assert on["knowledge_graph"] and ks.graph_config(on)["enabled"]
    off = ks.check_settings(knowledge_graph=False)
    assert ks.graph_config(off) is None and off["graph_instructions"] is None
    assert ks.graph_config({}) is None  # a build from before the graph
    with pytest.raises(ValueError, match="true or false"):
        ks.check_settings(knowledge_graph="yes")


def test_the_client_turns_the_graph_on_and_reads_the_graph_database():
    from f.dhr.O2 import graph

    seen = {}

    def handler(request):
        if request.url.path.endswith("/system/info"):
            return httpx.Response(
                200, json={"success": True, "data": {"graph_database_engine": "Not Enabled"}}
            )
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"success": True, "data": {"id": "kb1"}})

    wk = fake_weknora(handler)
    assert wk.graph_database() == "Not Enabled"
    wk.create_knowledge_base(
        name="n", description="d", chat_model_id="m1", extract_config=graph.extract_config()
    )
    assert seen["body"]["indexing_strategy"]["graph_enabled"] is True
    assert seen["body"]["extract_config"]["tags"] == graph.RELATION_TYPES
    wk.create_knowledge_base(name="n", description="d", chat_model_id="m1")
    assert seen["body"]["indexing_strategy"]["graph_enabled"] is False
    assert "extract_config" not in seen["body"]
