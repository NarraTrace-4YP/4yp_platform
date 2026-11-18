"""Tests for f/dhr/O1/load_reviews/load_reviews.py, using small made-up reviews (no real DHR text in the repo)."""

import hashlib
import json

import pytest

from f.dhr.O1.load_reviews import load_reviews as ingest


def make_review(pages):
    """A tiny review in the dataset's format: pages, each holding blocks."""
    return {
        "children": [
            {"id": f"/page/{n}/Page/{n}", "block_type": "Page", "children": blocks}
            for n, blocks in enumerate(pages)
        ],
        "metadata": {"page_stats": []},
    }


def make_dataset(root, reviews, listed_under_data=True):
    """Writes reviews into data/ plus a matching CHECKSUMS.sha256, like the real dataset."""
    (root / "data").mkdir(parents=True)
    lines = []
    for name, review in reviews.items():
        path = root / "data" / f"{name}.json"
        path.write_text(json.dumps(review), encoding="utf-8")
        listed = f"data/{name}.json" if listed_under_data else f"{name}.json"
        lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {listed}")
    (root / "CHECKSUMS.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return root


TEXT = {"id": "/page/0/Text/0", "block_type": "Text", "html": "<p>Made-up text.</p>"}
NESTED = {
    "id": "/page/0/ListGroup/1",
    "block_type": "ListGroup",
    "children": [{"id": "/page/0/ListItem/2", "block_type": "ListItem"}],
}


def test_matching_files_pass(tmp_path):
    root = make_dataset(tmp_path, {"AA - Somewhere - 2020_a - DHR": make_review([[TEXT]])})
    assert ingest.verify_checksums(root) == []


def test_paths_listed_without_data_folder_still_found(tmp_path):
    root = make_dataset(tmp_path, {"A": make_review([[TEXT]])}, listed_under_data=False)
    assert ingest.verify_checksums(root) == []


def test_changed_file_is_reported(tmp_path):
    root = make_dataset(tmp_path, {"A": make_review([[TEXT]])})
    (root / "data" / "A.json").write_text("{}", encoding="utf-8")
    assert ingest.verify_checksums(root) == ["doesn't match its checksum: data/A.json"]


def test_unlisted_and_missing_files_are_reported(tmp_path):
    root = make_dataset(tmp_path, {"A": make_review([[TEXT]]), "B": make_review([[TEXT]])})
    (root / "data" / "B.json").rename(root / "data" / "C.json")
    assert ingest.verify_checksums(root) == [
        "missing: data/B.json",
        "not in CHECKSUMS.sha256: data/C.json",
    ]


def test_bad_checksum_line_is_an_error(tmp_path):
    root = make_dataset(tmp_path, {"A": make_review([[TEXT]])})
    (root / "CHECKSUMS.sha256").write_text("not a checksum line\n", encoding="utf-8")
    with pytest.raises(ValueError, match="line 1"):
        ingest.read_checksums(root)


def test_read_document_counts_pages_and_blocks(tmp_path):
    root = make_dataset(
        tmp_path, {"AA - Somewhere - 2020_a - DHR": make_review([[TEXT], [NESTED], []])}
    )
    document = ingest.read_document(root / "data" / "AA - Somewhere - 2020_a - DHR.json", root)
    assert document.doc_id == "AA - Somewhere - 2020_a - DHR"
    assert document.file_path == "data/AA - Somewhere - 2020_a - DHR.json"
    assert (document.n_pages, document.n_blocks) == (3, 3)  # Text, ListGroup and its ListItem


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ({"children": []}, "has no pages"),
        ({"children": [TEXT]}, "only hold Page blocks"),
        ([1, 2, 3], "has no pages"),
    ],
)
def test_read_document_rejects_the_wrong_shape(tmp_path, content, message):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(content), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        ingest.read_document(path, tmp_path)


def test_read_document_rejects_nul_characters(tmp_path):
    path = tmp_path / "nul.json"
    path.write_text('{"children": [], "x": "a\\u0000b"}', encoding="utf-8")
    with pytest.raises(ValueError, match="u0000"):
        ingest.read_document(path, tmp_path)


def test_bad_dataset_stops_before_the_database(tmp_path, monkeypatch):
    root = make_dataset(tmp_path, {"A": make_review([[TEXT]])})
    (root / "data" / "A.json").write_text("{}", encoding="utf-8")
    monkeypatch.delenv("DHR_DATABASE_URL", raising=False)
    with pytest.raises(ingest.LoadError, match="checksum"):
        ingest.run(local_path=root, expected_documents=1)


def test_wrong_number_of_reviews_stops_the_load(tmp_path, monkeypatch):
    root = make_dataset(tmp_path, {"A": make_review([[TEXT]])})
    monkeypatch.delenv("DHR_DATABASE_URL", raising=False)
    with pytest.raises(ingest.LoadError, match="expected 100"):
        ingest.run(local_path=root, expected_documents=100)


# ----------------------------------------------------------------- DHRC-RAW's layout


def test_lower_case_checksums_file_is_found(tmp_path):
    # DHRC-RAW calls it checksums.sha256; problems name the file it really has.
    root = make_dataset(tmp_path, {"A": make_review([[TEXT]])})
    (root / "CHECKSUMS.sha256").rename(root / "checksums.sha256")
    assert ingest.verify_checksums(root) == []
    (root / "data" / "B.json").write_text("{}", encoding="utf-8")
    assert ingest.verify_checksums(root) == ["not in checksums.sha256: data/B.json"]


def test_no_checksums_file_stops_the_load(tmp_path):
    root = make_dataset(tmp_path, {"A": make_review([[TEXT]])})
    (root / "CHECKSUMS.sha256").unlink()
    with pytest.raises(ingest.LoadError, match="no CHECKSUMS.sha256"):
        ingest.verify_checksums(root)


def test_manifest_is_the_metadata_when_there_is_no_metadata_json(tmp_path):
    (tmp_path / "manifest.csv").write_text(
        "document_id,dhr_title,source_url,dhrc100_member\n"
        "A,Alex,https://example.org/download/abc123,true\n"
        "B,Sam,,false\n",
        encoding="utf-8",
    )
    assert ingest.read_dataset_metadata(tmp_path) == {
        "source": "manifest.csv",
        "documents": [
            {
                "document_id": "A",
                "dhr_title": "Alex",
                "source_url": "https://example.org/download/abc123",
                "dhrc100_member": "true",
            },
            {"document_id": "B", "dhr_title": "Sam", "source_url": None, "dhrc100_member": "false"},
        ],
    }


def test_metadata_json_wins_over_the_manifest(tmp_path):
    (tmp_path / "DHRC_METADATA.json").write_text('{"documents": []}', encoding="utf-8")
    (tmp_path / "manifest.csv").write_text("document_id\nA\n", encoding="utf-8")
    assert ingest.read_dataset_metadata(tmp_path) == {"documents": []}


def test_lenient_reading_skips_and_lists_bad_reviews(tmp_path):
    root = make_dataset(
        tmp_path, {"A": make_review([[TEXT]]), "B": {"children": []}, "C": make_review([[NESTED]])}
    )
    (root / "data" / "D.json").write_text("not json", encoding="utf-8")
    documents, skipped = ingest.read_documents(root, strict=False)
    assert [d.doc_id for d in documents] == ["A", "C"]
    assert len(skipped) == 2
    assert skipped[0] == "B.json: has no pages"
    assert skipped[1].startswith("D.json: ")


def test_strict_reading_stops_at_a_bad_review(tmp_path):
    root = make_dataset(tmp_path, {"A": make_review([[TEXT]]), "B": {"children": []}})
    with pytest.raises(ingest.LoadError, match="1 review\\(s\\) couldn't be read"):
        ingest.read_documents(root, strict=True)


def test_bad_review_in_dhrc_100_stops_before_the_database(tmp_path, monkeypatch):
    root = make_dataset(tmp_path, {"A": make_review([[TEXT]]), "B": {"children": []}})
    monkeypatch.delenv("DHR_DATABASE_URL", raising=False)
    with pytest.raises(ingest.LoadError, match="B.json: has no pages"):
        ingest.run("DHRC-100", local_path=root, expected_documents=2)


def test_unknown_dataset_is_refused(tmp_path):
    with pytest.raises(ValueError, match="DHRC-100, DHRC-RAW"):
        ingest.run("DHRC-200", local_path=tmp_path)


def test_each_dataset_has_its_usual_review_count(tmp_path, monkeypatch):
    root = make_dataset(tmp_path, {"A": make_review([[TEXT]])})
    monkeypatch.delenv("DHR_DATABASE_URL", raising=False)
    with pytest.raises(ingest.LoadError, match="expected 702"):
        ingest.run("DHRC-RAW", local_path=root)
