-- The DHRC-100 reviews, stored exactly as the dataset ships them.
-- Filled by the load_dhrc pipeline (dhr/ingest.py). Everything later is built from here.

-- One row per version of the dataset that has been loaded.
CREATE TABLE corpus.dataset_versions (
    revision          text PRIMARY KEY,                    -- Hugging Face commit id
    repo_id           text NOT NULL,                       -- e.g. 4YP/dhr-corpus-100-copy
    loaded_at         timestamptz NOT NULL DEFAULT now(),
    git_commit        text,                                -- the pipeline code that loaded it
    n_documents       integer NOT NULL,
    dataset_metadata  jsonb                                -- the dataset's own METADATA.json
);

-- One row per review, per version. "raw" is the review's JSON file, untouched.
CREATE TABLE corpus.documents (
    revision   text NOT NULL REFERENCES corpus.dataset_versions (revision) ON DELETE CASCADE,
    doc_id     text NOT NULL,      -- the file name without .json, e.g. "AB - Sandwell - 2017_a - DHR"
    file_path  text NOT NULL,      -- where the file sits in the dataset
    sha256     text NOT NULL,      -- matches the dataset's CHECKSUMS.sha256
    n_pages    integer NOT NULL,
    n_blocks   integer NOT NULL,
    raw        jsonb NOT NULL,
    PRIMARY KEY (revision, doc_id)
);

-- The most recently loaded version. Read this unless you need a specific older one.
CREATE VIEW corpus.current_documents AS
SELECT d.*
FROM corpus.documents AS d
WHERE d.revision = (
    SELECT revision FROM corpus.dataset_versions ORDER BY loaded_at DESC LIMIT 1
);
