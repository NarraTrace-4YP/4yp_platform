# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#     "psycopg[binary]==3.3.9",
#     "huggingface-hub==2.4.0",
#     "mlflow-skinny==3.18.0",
#     "wmill==1.875.0",
# ]
# ///
"""O1 stage 1: load reviews.

Download DHRC-100 or DHRC-RAW from Hugging Face, or read a local dataset folder.
Verify all listed checksums before writing. DHRC-100 rejects malformed reviews;
DHRC-RAW skips them and records the failures. Store each original review JSON
with its dataset revision so later stages can trace their inputs.

Reloading a revision replaces its documents and invalidates derived sections and
template candidates. Run grouping and candidate matching again after a reload.
``main`` is the Windmill entry point; the CLI also supports ``--local-path``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ...datasets import DATASETS, check_dataset
from ...db import apply_migrations, connect
from ...settings import ProgressBar, use_hugging_face_token, use_mlflow
from ...version import GIT_COMMIT

CHECKSUM_LINE = re.compile(r"^([0-9a-fA-F]{64}) [ *](.+)$")

# How far through the load each stage ends, for the progress bar (download is the first 25%).
Progress = Callable[[float], None]
DOWNLOADED, CHECKED, READ = 25, 45, 60


def no_progress(percent: float) -> None:
    """Ignore progress updates when no reporting callback was supplied."""
    pass


class LoadError(RuntimeError):
    """The dataset failed a check, so nothing was stored."""


def log_progress(message: str) -> None:
    """Flush a status line immediately so Windmill can display it during the run."""
    print(message, flush=True)


@dataclass
class Document:
    doc_id: str  # the file name without .json
    file_path: str  # relative to the dataset folder
    sha256: str
    n_pages: int
    n_blocks: int


# Checking the files.


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def checksums_file(root: Path) -> Path:
    """The dataset's checksums file: CHECKSUMS.sha256 (DHRC-100) or checksums.sha256."""
    for path in sorted(root.iterdir()):
        if path.is_file() and path.name.lower() == "checksums.sha256":
            return path
    raise LoadError("the dataset has no CHECKSUMS.sha256 file, so its files can't be checked")


def read_checksums(root: Path) -> dict[str, str]:
    """The dataset's checksums file, as {path inside the dataset: hash}."""
    checksums = {}
    path = checksums_file(root)
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip() or line.startswith("#"):
            continue
        match = CHECKSUM_LINE.match(line.rstrip())
        if not match:
            raise ValueError(f"{path.name} line {number} isn't 'hash  path': {line!r}")
        checksums[match.group(2).removeprefix("./")] = match.group(1).lower()
    return checksums


def resolve_dataset_file(root: Path, listed: str) -> Path:
    """Resolve a checksums entry relative to the dataset or its checksums folder."""
    path = root / listed
    return path if path.exists() else root / "data" / listed


def verify_checksums(root: Path, progress: Progress = no_progress) -> list[str]:
    """Every problem found: listed files missing or changed, and review files not listed."""
    problems = []
    listed_paths = set()
    name = checksums_file(root).name
    checksums = read_checksums(root)
    for number, (listed, expected) in enumerate(checksums.items(), start=1):
        progress(DOWNLOADED + (CHECKED - DOWNLOADED) * number / len(checksums))
        path = resolve_dataset_file(root, listed)
        listed_paths.add(path.resolve())
        if not path.is_file():
            problems.append(f"missing: {listed}")
        elif sha256_of(path) != expected:
            problems.append(f"doesn't match its checksum: {listed}")
    for path in review_files(root):
        if path.resolve() not in listed_paths:
            problems.append(f"not in {name}: {path.relative_to(root).as_posix()}")
    return problems


def review_files(root: Path) -> list[Path]:
    return sorted((root / "data").rglob("*.json"))


def count_blocks(block: dict) -> int:
    """How many blocks sit inside this one, at any depth."""
    return sum(1 + count_blocks(child) for child in block.get("children") or [])


def load_review(path: Path) -> dict:
    """One review file's JSON, refusing what the database can't store."""
    text = path.read_text(encoding="utf-8")
    if "\\u0000" in text:
        # The database can't store this character inside JSON; better to stop and look.
        raise ValueError(f"{path.name}: contains a \\u0000 character")
    return json.loads(text)


def read_document(path: Path, root: Path) -> Document:
    """Reads one review file and checks it has the expected shape: a list of pages."""
    raw = load_review(path)
    pages = raw.get("children") if isinstance(raw, dict) else None
    if not isinstance(pages, list) or not pages:
        raise ValueError(f"{path.name}: has no pages")
    if any(not isinstance(page, dict) or page.get("block_type") != "Page" for page in pages):
        raise ValueError(f"{path.name}: the top level should only hold Page blocks")
    return Document(
        doc_id=path.stem,
        file_path=path.relative_to(root).as_posix(),
        sha256=sha256_of(path),
        n_pages=len(pages),
        n_blocks=sum(count_blocks(page) for page in pages),
    )


def read_documents(
    root: Path, strict: bool, progress: Progress = no_progress
) -> tuple[list[Document], list[str]]:
    """Checks every review reads as a document. Returns those that do, and the problems.
    In strict mode any problem stops everything. Reviews are read one at a time and then
    let go, so even a large dataset needs little memory."""
    documents, problems = [], []
    paths = review_files(root)
    for number, path in enumerate(paths, start=1):
        progress(CHECKED + (READ - CHECKED) * number / len(paths))
        try:
            documents.append(read_document(path, root))
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as error:
            problems.append(str(error) if path.name in str(error) else f"{path.name}: {error}")
    if strict and problems:
        raise LoadError(
            f"{len(problems)} review(s) couldn't be read:\n  " + "\n  ".join(problems[:20])
        )
    return documents, problems


def read_dataset_metadata(root: Path) -> dict | list | None:
    """The dataset's own description of its reviews: METADATA.json (DHRC-100) or
    manifest.csv (DHRC-RAW), both stored as {"documents": [one entry per review]}."""
    candidates = sorted(root.glob("*METADATA.json"))
    if candidates:
        return json.loads(candidates[0].read_text(encoding="utf-8"))
    manifest = root / "manifest.csv"
    if manifest.is_file():
        with manifest.open(encoding="utf-8-sig", newline="") as handle:
            rows = [
                {k: v if v != "" else None for k, v in row.items()}
                for row in csv.DictReader(handle)
            ]
        return {"source": "manifest.csv", "documents": rows}
    return None


# Getting and storing it.


def download_dataset(repo_id: str, revision: str, target: Path) -> str:
    """Downloads one version of the dataset into target. Returns its exact commit id."""
    from huggingface_hub import HfApi, snapshot_download
    from huggingface_hub.errors import RepositoryNotFoundError, RevisionNotFoundError

    try:
        commit = HfApi().dataset_info(repo_id, revision=revision).sha
    except RepositoryNotFoundError as error:
        raise RuntimeError(
            f"can't see the dataset {repo_id}. Check HF_TOKEN is set and can read it."
        ) from error
    except RevisionNotFoundError as error:
        raise RuntimeError(f"{repo_id} has no version called {revision!r}") from error
    snapshot_download(repo_id, repo_type="dataset", revision=commit, local_dir=target)
    return commit


def store_dataset(
    conn,
    dataset,
    repo_id,
    revision,
    documents,
    root,
    dataset_metadata,
    git_commit,
    progress: Progress = no_progress,
) -> None:
    """Writes one dataset version, replacing it if it was loaded before. All or nothing.
    Each review is read from disk again as it's written, so only one is in memory."""
    from psycopg.types.json import Jsonb

    with conn.transaction():
        # Removing the version also removes its documents (ON DELETE CASCADE).
        conn.execute("DELETE FROM corpus.dataset_versions WHERE revision = %s", (revision,))
        conn.execute(
            "INSERT INTO corpus.dataset_versions"
            " (revision, repo_id, dataset, git_commit, n_documents, dataset_metadata)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (revision, repo_id, dataset, git_commit, len(documents), Jsonb(dataset_metadata)),
        )
        for number, d in enumerate(documents, start=1):
            conn.execute(
                "INSERT INTO corpus.documents"
                " (revision, doc_id, file_path, sha256, n_pages, n_blocks, raw)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    revision,
                    d.doc_id,
                    d.file_path,
                    d.sha256,
                    d.n_pages,
                    d.n_blocks,
                    Jsonb(load_review(root / d.file_path)),
                ),
            )
            progress(READ + (99 - READ) * number / len(documents))
            if number % 100 == 0:
                log_progress(f"      stored {number}/{len(documents)}")


def log_to_mlflow(summary: dict) -> str | None:
    """Records the load as a run in MLflow's 'corpus-ingest' experiment, if MLflow is set up."""
    if not use_mlflow():
        return None
    import mlflow

    mlflow.set_experiment("corpus-ingest")
    with mlflow.start_run(run_name=f"{summary['dataset']} {summary['revision'][:8]}") as run:
        mlflow.log_params(
            {k: summary[k] for k in ("dataset", "repo_id", "revision", "git_commit", "n_documents")}
        )
        mlflow.log_metrics(
            {k: summary[k] for k in ("n_documents", "n_pages", "n_blocks", "n_skipped")}
        )
        mlflow.set_tag("git_commit", summary["git_commit"])
        mlflow.set_tag("dataset", summary["dataset"])
    return run.info.run_id


# The whole run.


def run(
    dataset: str = "DHRC-100",
    revision: str = "main",
    expected_documents: int | None = None,
    local_path: Path | None = None,
    workdir: Path | None = None,
) -> dict:
    """Validate and store one dataset revision, then return load totals.

    Use the dataset's expected review count when expected_documents is None;
    set it to zero to disable that count check. All checksum checks precede writes."""
    settings = check_dataset(dataset)
    repo_id = settings["repo_id"]
    progress = ProgressBar()  # the run's progress bar in Windmill (does nothing elsewhere)
    if expected_documents is None:
        expected_documents = settings["reviews"]

    if local_path:
        root = local_path
        revision = "local-" + sha256_of(checksums_file(root))[:12]
        log_progress(f"Using the copy in {root} (recorded as {dataset} version {revision})")
    else:
        use_hugging_face_token()
        log_progress(f"Downloading {dataset} ({repo_id}) at {revision!r}...")
        root = workdir or Path(tempfile.mkdtemp(prefix="dhrc-"))
        progress(1)
        revision = download_dataset(repo_id, revision, root)
        log_progress(f"OK    Downloaded version {revision}")

    progress(DOWNLOADED)
    problems = verify_checksums(root, progress)
    if problems:
        raise LoadError(
            f"{len(problems)} file(s) failed the checksum check:\n  " + "\n  ".join(problems[:20])
        )
    log_progress(f"OK    Every file matches {checksums_file(root).name}")

    n_files = len(review_files(root))
    if expected_documents and n_files != expected_documents:
        raise LoadError(f"found {n_files} reviews, expected {expected_documents}")
    documents, skipped = read_documents(root, strict=settings["strict"], progress=progress)
    n_pages = sum(d.n_pages for d in documents)
    n_blocks = sum(d.n_blocks for d in documents)
    log_progress(f"OK    Read {len(documents)} reviews: {n_pages} pages, {n_blocks} blocks")
    if skipped:
        log_progress(f"NOTE  Skipped {len(skipped)} review(s) that don't read as documents:")
        for problem in skipped[:50]:
            log_progress(f"        {problem}")

    with connect() as conn:
        ran = apply_migrations(conn)
        if ran:
            log_progress(f"OK    Database tables updated: {', '.join(ran)}")
        metadata = read_dataset_metadata(root)
        store_dataset(
            conn, dataset, repo_id, revision, documents, root, metadata, GIT_COMMIT, progress
        )
    log_progress(f"OK    Stored in corpus.documents as {dataset} version {revision}")

    summary = {
        "dataset": dataset,
        "repo_id": repo_id,
        "revision": revision,
        "git_commit": GIT_COMMIT,
        "n_documents": len(documents),
        "n_pages": n_pages,
        "n_blocks": n_blocks,
        "n_skipped": len(skipped),
        "skipped": skipped,
    }
    run_id = log_to_mlflow(summary)
    if run_id:
        log_progress(f"OK    Logged to MLflow: run {run_id} in experiment 'corpus-ingest'")
        summary["mlflow_run_id"] = run_id
    return summary


def main(dataset: Literal["DHRC-100", "DHRC-RAW"] = "DHRC-100", revision: str = "main"):
    """Windmill runs this. dataset: DHRC-100 or DHRC-RAW. revision: a branch, tag or
    commit of that dataset on Hugging Face (main is the latest)."""
    sys.stdout.reconfigure(line_buffering=True)  # show progress in the log as it happens
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")  # they only clutter the log
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    with tempfile.TemporaryDirectory() as workdir:
        return run(dataset, revision, workdir=Path(workdir))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", default="DHRC-100", choices=sorted(DATASETS))
    parser.add_argument("--revision", default="main", help="dataset branch, tag or commit")
    parser.add_argument("--local-path", type=Path, help="load a copy already on disk")
    parser.add_argument(
        "--expected-documents", type=int, help="fail if the count differs (0: don't check)"
    )
    args = parser.parse_args()
    if args.local_path:
        run(args.dataset, args.revision, args.expected_documents, local_path=args.local_path)
    else:
        main(args.dataset, args.revision)
    log_progress("Done.")
