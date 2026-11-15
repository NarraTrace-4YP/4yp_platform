"""The project database's tables, as a numbered list of SQL changes ("migrations").

db.apply_migrations() runs, in order, every migration the database hasn't had yet, and
records it in public.schema_migrations. To change the database, add a new entry at the
END of the list (e.g. "003_blocks"). Never edit, rename or reorder one that has been
merged: the server has already run it and won't run it again.
"""

MIGRATIONS: list[tuple[str, str]] = [
    (
        "001_corpus_documents",
        """
-- The DHRC-100 reviews, stored exactly as the dataset ships them.
-- Filled by the load_dhrc script (f/dhr/load_dhrc.py). Everything later is built from here.

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
    -- doc_id: the file name without .json, e.g. "AB - Sandwell - 2017_a - DHR"
    doc_id     text NOT NULL,
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
""",
    ),
    (
        "002_library_case_metadata",
        """
-- Case details from the Home Office's DHR Library, for checking our pipeline's answers.
-- Filled by the collect_case_metadata script. Kept in the evaluation
-- schema so the wiki agent can never see them.
--
-- The library only shares case details as search filters, so each detail here means
-- "this review came back when searching for this filter option". A missing detail can
-- mean "no" or "not recorded"; library_filters lists every option that was searched, so
-- you can tell "not asked" apart from "asked, and not returned".

-- One row per collection run.
CREATE TABLE evaluation.library_snapshots (
    snapshot_id   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    collected_at  timestamptz NOT NULL DEFAULT now(),
    source_url    text NOT NULL,         -- https://homicide-review.homeoffice.gov.uk/
    git_commit    text,                  -- the pipeline code that collected it
    n_reviews     integer NOT NULL,      -- how many reviews the library listed
    n_filters     integer NOT NULL,      -- how many filter options were searched
    complete      boolean NOT NULL       -- false for a test run that searched only some
);

-- Every review the library listed, as its results page shows it.
CREATE TABLE evaluation.library_reviews (
    snapshot_id    bigint NOT NULL REFERENCES evaluation.library_snapshots ON DELETE CASCADE,
    library_id     text NOT NULL,        -- the id in its download link (/download/<id>)
    full_name      text NOT NULL,        -- e.g. "Debbie - Havering - 2023/a - DHR"
    title          text NOT NULL,        -- e.g. "Debbie"
    csp            text,                 -- Community Safety Partnership, e.g. "Havering"
    csp_region     text,                 -- e.g. "Greater London"
    uploaded       text,                 -- as shown, e.g. "November 2026"
    date_of_death  text,                 -- as shown, e.g. "May 2023"
    PRIMARY KEY (snapshot_id, library_id)
);

-- Every filter option that was searched, and how many reviews it returned.
CREATE TABLE evaluation.library_filters (
    snapshot_id  bigint NOT NULL REFERENCES evaluation.library_snapshots ON DELETE CASCADE,
    -- field: the site's field, e.g. "Victim-Specific Information.Victims.Sex of victim[]"
    field        text NOT NULL,
    value        text NOT NULL,          -- the site's value, e.g. "Female" or "16,19"
    label        text NOT NULL,          -- what the site shows, e.g. "Female" or "16 - 19"
    group_label  text NOT NULL,          -- where it sits on the site, e.g. "About the victim / Sex"
    n_results    integer NOT NULL,
    PRIMARY KEY (snapshot_id, field, value)
);

-- Which reviews each filter option returned: one row per review per detail.
CREATE TABLE evaluation.library_attributes (
    snapshot_id  bigint NOT NULL,
    library_id   text NOT NULL,
    field        text NOT NULL,
    value        text NOT NULL,
    PRIMARY KEY (snapshot_id, library_id, field, value),
    FOREIGN KEY (snapshot_id, library_id)
        REFERENCES evaluation.library_reviews ON DELETE CASCADE,
    FOREIGN KEY (snapshot_id, field, value)
        REFERENCES evaluation.library_filters ON DELETE CASCADE
);
CREATE INDEX ON evaluation.library_attributes (snapshot_id, field, value);

-- The views below always use the newest complete snapshot, and our newest DHRC version.

-- Which library review each of our reviews is. The dataset's METADATA.json records the
-- library download link for each one, so this is an exact match, not a guess by name.
CREATE VIEW evaluation.document_library_ids AS
SELECT doc ->> 'document_id' AS doc_id,
       substring(doc ->> 'source_url' FROM '/download/([0-9A-Za-z]+)') AS library_id
FROM corpus.dataset_versions AS v,
     jsonb_array_elements(
         CASE WHEN jsonb_typeof(v.dataset_metadata -> 'documents') = 'array'
              THEN v.dataset_metadata -> 'documents' ELSE '[]'::jsonb END
     ) AS doc
WHERE v.revision = (SELECT revision FROM corpus.dataset_versions ORDER BY loaded_at DESC LIMIT 1);

-- The newest complete snapshot (empty until the first full collection).
CREATE VIEW evaluation.library_latest AS
SELECT *
FROM evaluation.library_snapshots
WHERE complete
ORDER BY collected_at DESC, snapshot_id DESC
LIMIT 1;

-- Each case detail of each of our reviews, one row per detail. For example:
--   SELECT doc_id, label FROM evaluation.case_attributes
--   WHERE group_label = 'About the death / Method of killing';
CREATE VIEW evaluation.case_attributes AS
SELECT d.doc_id, a.library_id, f.group_label, f.label, f.field, f.value
FROM evaluation.document_library_ids AS d
JOIN evaluation.library_attributes AS a
    ON a.library_id = d.library_id
   AND a.snapshot_id = (SELECT snapshot_id FROM evaluation.library_latest)
JOIN evaluation.library_filters AS f
    ON f.snapshot_id = a.snapshot_id AND f.field = a.field AND f.value = a.value;

-- One row per review of ours: what the library lists for it, plus every case detail as
-- JSON ({"About the victim / Sex": ["Female"], ...}). Reviews the library doesn't have
-- still appear, with empty library columns, so gaps are visible.
CREATE VIEW evaluation.case_metadata AS
SELECT d.doc_id,
       d.library_id,
       r.full_name,
       r.title,
       r.csp,
       r.csp_region,
       r.uploaded,
       r.date_of_death,
       COALESCE(
           (SELECT jsonb_object_agg(g.group_label, g.labels)
            FROM (SELECT c.group_label, jsonb_agg(c.label ORDER BY c.label) AS labels
                  FROM evaluation.case_attributes AS c
                  WHERE c.doc_id = d.doc_id
                  GROUP BY c.group_label) AS g),
           '{}'::jsonb
       ) AS details
FROM evaluation.document_library_ids AS d
LEFT JOIN evaluation.library_reviews AS r
    ON r.library_id = d.library_id
   AND r.snapshot_id = (SELECT snapshot_id FROM evaluation.library_latest);
""",
    ),
]
