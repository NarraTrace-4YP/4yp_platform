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
    assert review["n_pages"] == 1  # a review uploaded in parts (25 Nov builds) is one row
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
    assert "wiki: nothing waiting" in nearly["line"]


def test_files_waiting_for_follow_up_jobs_count_as_read():
    """WeKnora keeps a file "finalizing" until its summary, graph and wiki jobs have run;
    progress follows those jobs instead of sitting at 5%."""
    from f.dhr.O2 import progress

    start = progress.build_progress({"finalizing": 10}, {"pending_tasks": 10}, 1, 0, None, 10, 2500)
    assert "10 of 10 reviews read by WeKnora, 0 completely done" in start["line"]
    assert "0 of 2,500 follow-up jobs done" in start["line"] and start["percent"] == 15
    assert "wiki: 10 reviews waiting to be written up" in start["line"]
    half = progress.build_progress(
        {"finalizing": 6, "completed": 4}, {"pending_tasks": 6}, 80, 100, None, 10, 1250, 2500
    )
    assert half["max_jobs"] == 2500 and 50 < half["percent"] < 60
    end = progress.build_progress(
        {"completed": 10}, {"pending_tasks": 0}, 200, 300, None, 10, 0, 2500
    )
    assert end["percent"] == 99


def test_the_gateway_line_reads_its_status():
    from f.dhr.llm import describe_gateway

    assert describe_gateway({"error": "can't"}) == "model: can't"
    assert describe_gateway({"user": "me", "run_id": None}).startswith("model: not running yet")
    line = describe_gateway(
        {
            "run_id": "gw-1",
            "slurm_job_id": "123",
            "job_state": "answering",
            "calls_with_the_job": 4,
            "calls_waiting_to_send": 1,
            "usage": {"calls": 1234, "completion_tokens": 56789},
        }
    )
    assert line == (
        "model on Blythe (SLURM job 123): answering, working on 4 calls, 1 about to be sent;"
        " 1,234 calls answered in total"
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


def test_a_long_review_is_uploaded_whole():
    from f.dhr.O2.kb_settings import WEKNORA_WIKI_READ_LIMIT

    blocks, levels = long_review()
    text, _ = reviews.render_markdown("R", blocks, levels)
    assert len(text) > WEKNORA_WIKI_READ_LIMIT // 8 and text.startswith("# R\n")
    assert not hasattr(reviews, "split_into_parts")


def test_knowledge_base_settings_are_checked():
    from f.dhr.O2 import kb_settings as ks

    s = ks.check_settings()
    assert "part_chars" not in s and ks.chunking_config(s) == {
        "strategy": "auto",
        "chunk_size": 512,
        "chunk_overlap": 80,
    }
    assert ks.wiki_tuning(s) == {}
    s = ks.check_settings("heading", 2000, 200, ["\n## ", "\n\n"], 10, 4, 4, 2)
    assert ks.chunking_config(s)["separators"] == ["\n## ", "\n\n"]
    assert ks.wiki_tuning(s) == {
        "ingest_batch_size": 10,
        "ingest_map_parallel": 4,
        "ingest_reduce_parallel": 4,
        "ingest_max_inflight": 2,
    }
    for bad, match in [
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


# --- Knowledge graph copy (25 Nov 2026) ------------------------------------------------------


def test_the_neo4j_label_matches_weknoras():
    from f.dhr.O2 import kgraph

    kb = "0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0"
    assert kgraph.label(kb) == "ENTITY0b1c2d3e_4f50_6172_8394_a5b6c7d8e9f0"
    with pytest.raises(ValueError):
        kgraph.label("x` MATCH (n) DETACH DELETE n //")


def test_a_reviews_parts_are_merged_and_reviews_kept_apart():
    from f.dhr.O2 import kgraph

    nodes = [
        {"name": "Adult A", "kg": "k1", "attributes": ["The victim"], "n_chunks": 2},
        {
            "name": "Adult A",
            "kg": "k2",
            "attributes": ["The victim", "Lived in Northtown"],
            "n_chunks": 1,
        },
        {"name": "Adult A", "kg": "k3", "attributes": [], "n_chunks": 1},
        {"name": "GP", "kg": "k1", "attributes": [], "n_chunks": 1},
        {"name": "Stray", "kg": "elsewhere", "attributes": [], "n_chunks": 1},
    ]
    relations = [
        {"source": "Adult A", "target": "GP", "kg": "k1", "type": "Disclosed abuse to"},
        {"source": "Adult A", "target": "GP", "kg": "k2", "type": "Disclosed abuse to"},
        {"source": "Adult A", "target": "Adult A", "kg": "k1", "type": "Lives with"},
    ]
    node_rows, rel_rows = kgraph.merge_graph(nodes, relations, {"k1": "R1", "k2": "R1", "k3": "R2"})
    assert node_rows == [
        ("R1", "Adult A", ["The victim", "Lived in Northtown"], 3, 2),
        ("R1", "GP", [], 1, 1),
        ("R2", "Adult A", [], 1, 1),
    ]
    # k2's relation names a GP that only k1 found, but both are review R1, so it counts once.
    assert rel_rows == [("R1", "Adult A", "GP", "Disclosed abuse to")]


def test_fetch_graph_asks_for_this_knowledge_base_only():
    from f.dhr.O2 import kgraph

    seen = []

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def run(self, query):
            seen.append(query)
            return (
                [{"name": "x", "kg": "k", "attributes": [], "n_chunks": 0}]
                if "RETURN n.name" in query
                else []
            )

    class Driver:
        def session(self):
            return Session()

    nodes, relations = kgraph.fetch_graph("0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0", driver=Driver())
    assert len(nodes) == 1 and relations == []
    assert all("`ENTITY0b1c2d3e_4f50_6172_8394_a5b6c7d8e9f0`" in q for q in seen)


def test_an_unreachable_graph_database_does_not_stop_the_check(monkeypatch):
    from f.dhr.O2 import kgraph

    def unavailable(kb_id):
        raise kgraph.GraphUnavailable("no password")

    monkeypatch.setattr(kgraph, "fetch_graph", unavailable)
    assert kgraph.copy_graph(None, "w", "kb") == {"error": "no password"}


def test_the_knowledge_graph_is_saved_and_served(monkeypatch):
    import os

    url = os.environ.get("DHR_TEST_DATABASE_URL")
    if not url:
        pytest.skip("set DHR_TEST_DATABASE_URL to an empty test database to run this")
    os.environ["DHR_DATABASE_URL"] = url
    from f.dhr.db import apply_migrations, connect
    from f.dhr.O2 import kgraph
    from f.dhr.O2.app_backend import get_kgraph

    with connect(url) as conn:
        for schema in ("corpus", "evaluation", "scratch"):
            conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        apply_migrations(conn)
        conn.execute("DELETE FROM wiki.instances WHERE instance_id = 'kg1'")
        conn.execute(
            "INSERT INTO wiki.instances (instance_id, name, created_by, dataset, reviews, model,"
            " settings) VALUES ('kg1', 'n', 'me', 'DHRC-100', 'pilot', 'm', '{}')"
        )
        for part, k in ((1, "k1"), (2, "k2")):
            conn.execute(
                "INSERT INTO wiki.documents (instance_id, doc_id, part, n_parts, dataset, revision,"
                " file_name, sha256, n_chars, knowledge_id) VALUES ('kg1', 'R1', %s, 2, 'DHRC-100',"
                " 'r', %s, 'h', 5, %s)",
                (part, f"R1 (part {part} of 2).md", k),
            )
        nodes = [
            {"name": "Adult A", "kg": "k1", "attributes": ["The victim"], "n_chunks": 1},
            {"name": "Police", "kg": "k2", "attributes": [], "n_chunks": 1},
            {"name": "GP", "kg": "k2", "attributes": [], "n_chunks": 1},
        ]
        relations = [{"source": "Police", "target": "GP", "kg": "k2", "type": "Referred to"}]
        assert kgraph.save_graph(conn, "kg1", nodes, relations) == {"nodes": 3, "relations": 1}
        assert kgraph.save_graph(conn, "kg1", nodes, relations)["nodes"] == 3  # replaces
    graph = get_kgraph.main("kg1")
    assert graph["total_nodes"] == 3 and not graph["truncated"]
    assert graph["nodes"][0]["degree"] == 1  # best-connected first
    assert graph["links"] == [{"source": "R1␟Police", "target": "R1␟GP", "type": "Referred to"}]
    assert graph["relation_types"] == {"Referred to": 1}
    assert get_kgraph.main("kg1", max_nodes=1)["truncated"]


def test_the_server_gives_windmill_the_graph_password():
    from pathlib import Path

    server = (Path(__file__).parent.parent / "server/windmill.sh").read_text(encoding="utf-8")
    assert "f/dhr/weknora_graph_password" in server and "WEKNORA_GRAPH_PASSWORD" in server


def test_the_client_deletes_a_knowledge_base_once():
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if len(calls) == 1:
            return httpx.Response(200, json={"success": True})
        return httpx.Response(404, json={"success": False, "error": "not found"})

    wk = fake_weknora(handler)
    assert wk.delete_knowledge_base("kb1") is True
    assert wk.delete_knowledge_base("kb1") is False  # already gone
    assert calls[0] == ("DELETE", "/api/v1/knowledge-bases/kb1")


def test_delete_graph_removes_this_knowledge_base_in_batches():
    from f.dhr.O2 import kgraph

    seen = []
    left = [2500]

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def run(self, query):
            seen.append(query)
            n = min(1000, left[0])
            left[0] -= n
            return type("R", (), {"single": lambda self: {"n": n}})()

    class Driver:
        def session(self):
            return Session()

    assert kgraph.delete_graph("0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0", driver=Driver()) == 2500
    assert len(seen) == 4 and all(
        "`ENTITY0b1c2d3e_4f50_6172_8394_a5b6c7d8e9f0`" in q and "DETACH DELETE" in q for q in seen
    )


def test_a_wiki_is_deleted_everywhere(monkeypatch):
    import os

    url = os.environ.get("DHR_TEST_DATABASE_URL")
    if not url:
        pytest.skip("set DHR_TEST_DATABASE_URL to an empty test database to run this")
    os.environ["DHR_DATABASE_URL"] = url
    from f.dhr.db import apply_migrations, connect
    from f.dhr.O2 import kgraph
    from f.dhr.O2.delete_wiki import delete_wiki

    with connect(url) as conn:
        for schema in ("corpus", "evaluation", "scratch"):
            conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        apply_migrations(conn)
        conn.execute("DELETE FROM wiki.instances WHERE instance_id IN ('del1', 'keep1')")
        for instance_id, owner in (("del1", "me"), ("keep1", "me")):
            conn.execute(
                "INSERT INTO wiki.instances (instance_id, name, created_by, dataset, reviews, model,"
                " settings, weknora_kb_id, flow_job_id) VALUES (%s, 'n', %s, 'DHRC-100', 'pilot',"
                " 'm', '{}', %s, 'job1')",
                (instance_id, owner, "0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0"),
            )
            conn.execute(
                "INSERT INTO wiki.documents (instance_id, doc_id, dataset, revision, file_name,"
                " sha256, n_chars, knowledge_id) VALUES (%s, 'R1', 'DHRC-100', 'r', 'R1.md', 'h',"
                " 5, 'k1')",
                (instance_id,),
            )
            kgraph.save_graph(conn, instance_id, [{"name": "A", "kg": "k1"}], [])
    monkeypatch.setenv("WM_USERNAME", "me")
    deleted = []

    class FakeWeKnora:
        def delete_knowledge_base(self, kb_id):
            deleted.append(kb_id)
            return True

    monkeypatch.setattr(delete_wiki, "WeKnora", FakeWeKnora)
    monkeypatch.setattr(delete_wiki, "delete_graph", lambda kb_id: 7)
    monkeypatch.setattr(delete_wiki, "stop_flow", lambda job: f"cancelled {job}")
    asked = []
    monkeypatch.setattr(
        delete_wiki, "stop_model", lambda others: asked.append(others) or "asked it to stop"
    )

    with pytest.raises(ValueError, match="both"):
        delete_wiki.main("del1", "del2")  # the confirmation must match
    monkeypatch.setenv("WM_USERNAME", "someone-else")
    monkeypatch.setattr(delete_wiki, "is_admin", lambda: False)
    with pytest.raises(ValueError, match="only they"):
        delete_wiki.main("del1", "del1")
    assert deleted == []
    # A workspace admin may delete someone else's build, but leaves their model job alone.
    monkeypatch.setattr(delete_wiki, "is_admin", lambda: True)
    result = delete_wiki.main("keep1", "keep1")
    assert "left me's model job alone" in result["model_job"] and asked == []
    with connect(url) as conn:
        conn.execute(
            "INSERT INTO wiki.instances (instance_id, name, created_by, dataset, reviews, model,"
            " settings) VALUES ('keep1', 'n', 'me', 'DHRC-100', 'pilot', 'm', '{}')"
        )
        conn.execute(
            "INSERT INTO wiki.documents (instance_id, doc_id, dataset, revision, file_name,"
            " sha256, n_chars, knowledge_id) VALUES ('keep1', 'R1', 'DHRC-100', 'r', 'R1.md',"
            " 'h', 5, 'k1')"
        )
    deleted.clear()

    monkeypatch.setenv("WM_USERNAME", "me")
    result = delete_wiki.main("del1", "del1")
    assert deleted == ["0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0"]
    assert result["database"] == {"documents": 1, "pages": 0, "knowledge_graph_entities": 1}
    assert "7 entities" in result["graph_database"] and result["pipeline"] == "cancelled job1"
    assert result["model_job"] == "asked it to stop" and asked == [[]]  # keep1 isn't building
    with connect(url) as conn:
        for table in ("instances", "documents", "kg_nodes"):
            assert conn.execute(
                f"SELECT count(*) FROM wiki.{table} WHERE instance_id = 'del1'"
            ).fetchone() == (0,)
        # Other builds are untouched.
        assert conn.execute(
            "SELECT count(*) FROM wiki.documents WHERE instance_id = 'keep1'"
        ).fetchone() == (1,)
    with pytest.raises(ValueError, match="No wiki build"):
        delete_wiki.main("del1", "del1")


def test_the_model_job_is_tidied_up_unless_another_build_needs_it(monkeypatch):
    from f.dhr.O2.delete_wiki import delete_wiki

    stopped = []

    class FakeBlythe:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def stop_server(self, run_id):
            stopped.append(run_id)

    monkeypatch.setattr(delete_wiki, "Blythe", FakeBlythe)
    status = {"run_id": "gw-1", "slurm_job_id": "42", "job_state": "waiting"}
    monkeypatch.setattr(delete_wiki, "gateway_status", lambda: status)
    assert "still use it" in delete_wiki.stop_model(["wiki-2"]) and stopped == []
    assert "SLURM job 42" in delete_wiki.stop_model([]) and stopped == ["gw-1"]
    status.update(job_state="stopped")
    assert "already ended" in delete_wiki.stop_model([])
    status.update(run_id=None)
    assert "no model job" in delete_wiki.stop_model([])
    monkeypatch.setattr(delete_wiki, "gateway_status", lambda: {"error": "can't ask"})
    assert "stops by itself" in delete_wiki.stop_model([]) and stopped == ["gw-1"]


# --- folders kept in English (f/dhr/O2/folders.py) ---------------------------------------


class FakeFolders:
    """WeKnora's folder tree and pages, as far as f/dhr/O2/folders.py uses them."""

    def __init__(self):
        self.folders, self.pages, self.n = {}, [], 0

    def add(self, name, parent=""):
        self.n += 1
        fid = f"f{self.n}"
        depth = 1 + (self.folders[parent]["depth"] if parent else 0)
        self.folders[fid] = {"id": fid, "name": name, "parent_id": parent, "depth": depth}
        return fid

    def path(self, fid):
        f = self.folders[fid]
        return (self.path(f["parent_id"]) + "/" if f["parent_id"] else "") + f["name"]

    def wiki_folders(self, kb_id, parent_id=""):
        kids = [f for f in self.folders.values() if f["parent_id"] == parent_id]
        return [
            {
                **f,
                "path": self.path(f["id"]),
                "has_children": any(g["parent_id"] == f["id"] for g in self.folders.values()),
            }
            for f in kids
        ]

    def create_wiki_folder(self, kb_id, name, parent_id=""):
        if any(f["name"] == name and f["parent_id"] == parent_id for f in self.folders.values()):
            raise RuntimeError("WeKnora answered 409: conflict")
        return {"id": self.add(name, parent_id)}

    def update_wiki_folder(self, kb_id, folder_id, *, name="", parent_id=None):
        f = self.folders[folder_id]
        target = f["parent_id"] if parent_id is None else parent_id
        new = name or f["name"]
        if any(
            g["name"] == new and g["parent_id"] == target and g["id"] != folder_id
            for g in self.folders.values()
        ):
            raise RuntimeError("WeKnora answered 409: conflict")
        f.update(name=new, parent_id=target)

    def delete_wiki_folder(self, kb_id, folder_id):
        assert not any(g["parent_id"] == folder_id for g in self.folders.values())
        assert not any(p["folder_id"] == folder_id for p in self.pages)
        del self.folders[folder_id]

    def move_wiki_page(self, kb_id, slug, folder_id):
        next(p for p in self.pages if p["slug"] == slug)["folder_id"] = folder_id

    def wiki_pages(self, kb_id):
        return [dict(p) for p in self.pages]


def test_seed_folders_makes_only_the_missing_ones():
    from f.dhr.O2.folders import SEED_FOLDERS, seed_folders

    wk = FakeFolders()
    wk.add("People")
    made = seed_folders(wk, "kb")
    assert "People" not in made and len(made) == len(SEED_FOLDERS) - 1
    assert seed_folders(wk, "kb") == []


def test_english_folders_renames_and_merges():
    import json

    from f.dhr.O2.folders import english_folders, has_chinese

    wk = FakeFolders()
    health_en = wk.add("Health & Wellbeing")
    health_zh = wk.add("健康与福祉")
    sub_zh = wk.add("物质滥用", health_zh)
    law_zh = wk.add("法律概念")
    wk.pages = [
        {"slug": "concept/alcohol", "folder_id": sub_zh},
        {"slug": "concept/wellbeing", "folder_id": health_zh},
        {"slug": "concept/abh", "folder_id": law_zh},
    ]
    english = {
        "健康与福祉": "Health & Wellbeing",
        "物质滥用": "Substance Misuse",
        "法律概念": "Legal Concepts",
    }
    prompts = []

    def ask(prompt):
        prompts.append(prompt)
        out = []
        for line in prompt.splitlines():
            if line.startswith("- id "):
                fid, rest = line[5:].split(":", 1)
                name = rest.split("→", 1)[1].split("|", 1)[0].strip()
                out.append({"id": fid, "english": english[name]})
        return json.dumps({"translations": out})

    result = english_folders(wk, "kb", ask=ask)
    assert result == {"chinese": 3, "renamed": 2, "merged": 1, "pages_moved": 1, "left": 0}
    assert "English beside it: Health & Wellbeing" in prompts[0]
    assert len(prompts) == 2  # top level, then the sub-folder
    assert not any(has_chinese(f["name"]) for f in wk.folders.values())
    assert health_zh not in wk.folders
    assert wk.folders[sub_zh]["parent_id"] == health_en
    assert wk.path(sub_zh) == "Health & Wellbeing/Substance Misuse"
    assert {p["slug"]: p["folder_id"] for p in wk.pages}["concept/wellbeing"] == health_en
    assert english_folders(wk, "kb", ask=ask)["chinese"] == 0  # nothing left, no call
    assert len(prompts) == 2


def test_english_folders_ignores_bad_answers():
    from f.dhr.O2.folders import english_folders

    wk = FakeFolders()
    wk.add("案例与人物")
    result = english_folders(
        wk, "kb", ask=lambda p: '{"translations": [{"id": "f1", "english": "案例"}]}'
    )
    assert result["renamed"] == 0 and result["left"] == 1
