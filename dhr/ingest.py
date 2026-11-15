"""Loads the DHRC-100 dataset from Hugging Face into the database, untouched.

    python -m dhr.ingest                        # the latest version on Hugging Face
    python -m dhr.ingest --revision <commit>    # an exact version
    python -m dhr.ingest --local-path <folder>  # a copy already on disk (no download)

Every review goes into corpus.documents exactly as the dataset has it, next to the dataset
version (the Hugging Face commit) it came from. Nothing is written until every file has
passed two checks: it matches the dataset's own CHECKSUMS.sha256, and it reads as a
document (pages containing blocks). Loading the same version again replaces it, so it is
always safe to re-run.

Needs: DHR_DATABASE_URL, and HF_TOKEN (read access to the private dataset) unless
--local-path is used. If MLFLOW_TRACKING_URI is set, the result is logged to MLflow too.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

DEFAULT_REPO_ID = "4YP/dhr-corpus-100-copy"
CHECKSUM_LINE = re.compile(r"^([0-9a-fA-F]{64}) [ *](.+)$")


@dataclass
class Document:
    doc_id: str  # the file name without .json
    file_path: str  # relative to the dataset folder
    sha256: str
    n_pages: int
    n_blocks: int
    raw: dict


# ----------------------------------------------------------------- checking the files


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_checksums(root: Path) -> dict[str, str]:
    """The dataset's CHECKSUMS.sha256, as {path inside the dataset: hash}."""
    checksums = {}
    lines = (root / "CHECKSUMS.sha256").read_text(encoding="utf-8").splitlines()
    for number, line in enumerate(lines, start=1):
        if not line.strip() or line.startswith("#"):
            continue
        match = CHECKSUM_LINE.match(line.rstrip())
        if not match:
            raise ValueError(f"CHECKSUMS.sha256 line {number} isn't 'hash  path': {line!r}")
        checksums[match.group(2).removeprefix("./")] = match.group(1).lower()
    return checksums


def locate(root: Path, listed: str) -> Path:
    """Where a file named in CHECKSUMS.sha256 is (paths may or may not include data/)."""
    path = root / listed
    return path if path.exists() else root / "data" / listed


def verify_checksums(root: Path) -> list[str]:
    """Every problem found: listed files missing or changed, and review files not listed."""
    problems = []
    listed_paths = set()
    for listed, expected in read_checksums(root).items():
        path = locate(root, listed)
        listed_paths.add(path.resolve())
        if not path.is_file():
            problems.append(f"missing: {listed}")
        elif sha256_of(path) != expected:
            problems.append(f"doesn't match its checksum: {listed}")
    for path in sorted((root / "data").rglob("*.json")):
        if path.resolve() not in listed_paths:
            problems.append(f"not in CHECKSUMS.sha256: {path.relative_to(root).as_posix()}")
    return problems


def count_blocks(block: dict) -> int:
    """How many blocks sit inside this one, at any depth."""
    return sum(1 + count_blocks(child) for child in block.get("children") or [])


def read_document(path: Path, root: Path) -> Document:
    """Reads one review file and checks it has the expected shape: a list of pages."""
    text = path.read_text(encoding="utf-8")
    if "\\u0000" in text:
        # The database can't store this character inside JSON; better to stop and look.
        raise ValueError(f"{path.name}: contains a \\u0000 character")
    raw = json.loads(text)
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
        raw=raw,
    )


def read_dataset_metadata(root: Path) -> dict | list | None:
    """The dataset's own METADATA.json at the top level, if it has one."""
    candidates = sorted(root.glob("*METADATA.json"))
    return json.loads(candidates[0].read_text(encoding="utf-8")) if candidates else None


# ----------------------------------------------------------------- getting and storing it


def download(repo_id: str, revision: str, target: Path) -> str:
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


def store(conn, repo_id, revision, documents, dataset_metadata, git_commit) -> None:
    """Writes one dataset version, replacing it if it was loaded before. All or nothing."""
    from psycopg.types.json import Jsonb

    with conn.transaction():
        # Removing the version also removes its documents (ON DELETE CASCADE).
        conn.execute("DELETE FROM corpus.dataset_versions WHERE revision = %s", (revision,))
        conn.execute(
            "INSERT INTO corpus.dataset_versions"
            " (revision, repo_id, git_commit, n_documents, dataset_metadata)"
            " VALUES (%s, %s, %s, %s, %s)",
            (revision, repo_id, git_commit, len(documents), Jsonb(dataset_metadata)),
        )
        with conn.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO corpus.documents"
                " (revision, doc_id, file_path, sha256, n_pages, n_blocks, raw)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s)",
                [
                    (revision, d.doc_id, d.file_path, d.sha256, d.n_pages, d.n_blocks, Jsonb(d.raw))
                    for d in documents
                ],
            )


def log_to_mlflow(summary: dict) -> str | None:
    """Records the load as a run in MLflow's 'corpus-ingest' experiment, if MLflow is set up."""
    if not os.environ.get("MLFLOW_TRACKING_URI"):
        return None
    import mlflow

    mlflow.set_experiment("corpus-ingest")
    with mlflow.start_run(run_name=f"load {summary['revision'][:8]}") as run:
        mlflow.log_params(
            {k: summary[k] for k in ("repo_id", "revision", "git_commit", "n_documents")}
        )
        mlflow.log_metrics({k: summary[k] for k in ("n_documents", "n_pages", "n_blocks")})
        mlflow.set_tag("git_commit", summary["git_commit"])
    return run.info.run_id


# ----------------------------------------------------------------- the whole run


def run(args: argparse.Namespace, workdir: Path) -> int:
    from dhr import db

    git_commit = os.environ.get("DHR_GIT_COMMIT", "unknown")

    if args.local_path:
        root = args.local_path
        revision = "local-" + sha256_of(root / "CHECKSUMS.sha256")[:12]
        print(f"Using the copy in {root} (recorded as version {revision})")
    else:
        print(f"Downloading {args.repo_id} at {args.revision!r}...")
        root = workdir
        revision = download(args.repo_id, args.revision, root)
        print(f"OK    Downloaded version {revision}")

    problems = verify_checksums(root)
    if problems:
        print(f"FAIL  Checksums: {len(problems)} problem(s)")
        for problem in problems[:20]:
            print(f"        {problem}")
        return 1
    print("OK    Every file matches CHECKSUMS.sha256")

    files = sorted((root / "data").rglob("*.json"))
    documents, problems = [], []
    for path in files:
        try:
            documents.append(read_document(path, root))
        except (ValueError, json.JSONDecodeError) as error:
            problems.append(str(error))
    if problems:
        print(f"FAIL  Reading reviews: {len(problems)} problem(s)")
        for problem in problems[:20]:
            print(f"        {problem}")
        return 1
    if args.expected_documents and len(documents) != args.expected_documents:
        print(f"FAIL  Found {len(documents)} reviews, expected {args.expected_documents}")
        return 1
    n_pages = sum(d.n_pages for d in documents)
    n_blocks = sum(d.n_blocks for d in documents)
    print(f"OK    Read {len(documents)} reviews: {n_pages} pages, {n_blocks} blocks")

    with db.connect() as conn:
        ran = db.apply_migrations(conn)
        if ran:
            print(f"OK    Database tables updated: {', '.join(ran)}")
        store(conn, args.repo_id, revision, documents, read_dataset_metadata(root), git_commit)
    print(f"OK    Stored in corpus.documents as version {revision}")

    summary = {
        "repo_id": args.repo_id,
        "revision": revision,
        "git_commit": git_commit,
        "n_documents": len(documents),
        "n_pages": n_pages,
        "n_blocks": n_blocks,
    }
    run_id = log_to_mlflow(summary)
    if run_id:
        print(f"OK    Logged to MLflow: run {run_id} in experiment 'corpus-ingest'")
    print("Done.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo-id", default=os.environ.get("DHRC_REPO_ID", DEFAULT_REPO_ID))
    parser.add_argument("--revision", default="main", help="dataset branch, tag or commit")
    parser.add_argument("--local-path", type=Path, help="load a copy already on disk")
    parser.add_argument(
        "--expected-documents", type=int, default=100, help="fail if the count differs (0: skip)"
    )
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory() as workdir:
        return run(args, Path(workdir))


if __name__ == "__main__":
    sys.exit(main())
