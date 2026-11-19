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
    (
        "003_datasets",
        """
-- Several datasets now sit side by side: DHRC-100 (human-validated) and DHRC-RAW (702 raw
-- extractions, including raw versions of the same 100 reviews under the same doc_id).
-- Each loaded version says which dataset it is, and every "current" view gives the newest
-- version of each dataset, with a dataset column to choose by.
ALTER TABLE corpus.dataset_versions ADD COLUMN dataset text;
UPDATE corpus.dataset_versions
SET dataset = CASE WHEN repo_id = '4YP/dhr-corpus-raw-copy' THEN 'DHRC-RAW' ELSE 'DHRC-100' END;
ALTER TABLE corpus.dataset_versions ALTER COLUMN dataset SET NOT NULL;

-- The newest version loaded of each dataset.
CREATE VIEW corpus.current_versions AS
SELECT DISTINCT ON (dataset) *
FROM corpus.dataset_versions
ORDER BY dataset, loaded_at DESC;

-- The reviews in the newest version of each dataset. Pick with WHERE dataset = 'DHRC-100'.
CREATE OR REPLACE VIEW corpus.current_documents AS
SELECT d.*, v.dataset
FROM corpus.documents AS d
JOIN corpus.current_versions AS v ON v.revision = d.revision;

-- Which library review each of our reviews is, per dataset, from the dataset's own
-- metadata (METADATA.json for DHRC-100, manifest.csv for DHRC-RAW). Reviews from outside
-- the Home Office library (no /download/ link) have no library_id. The dataset_* columns
-- are what the dataset itself says, for reviews the library doesn't have. "loaded" is
-- false for a review whose file was skipped because it couldn't be read. "source" is where
-- the review was published, from its link: 'Home Office website' (the DHR Library),
-- 'HALT' (the HALT study's repository, domestichomicide-halt.co.uk) or 'Elsewhere' (such
-- as a council's website); empty if the dataset gives no link.
CREATE OR REPLACE VIEW evaluation.document_library_ids AS
SELECT doc ->> 'document_id' AS doc_id,
       substring(doc ->> 'source_url' FROM '/download/([0-9A-Za-z]+)') AS library_id,
       v.dataset,
       doc ->> 'dhr_title' AS dataset_title,
       doc ->> 'csp' AS dataset_csp,
       doc ->> 'csp_region' AS dataset_csp_region,
       doc ->> 'date_of_death' AS dataset_date_of_death,
       doc ->> 'source_url' AS source_url,
       EXISTS (SELECT 1 FROM corpus.documents AS f
               WHERE f.revision = v.revision AND f.doc_id = doc ->> 'document_id') AS loaded,
       CASE
           WHEN doc ->> 'source_url' IS NULL THEN NULL
           WHEN substring(doc ->> 'source_url' FROM '^[A-Za-z]+://([^/:?#]+)')
                ~* '(^|[.])homicide-review[.]homeoffice[.]gov[.]uk$' THEN 'Home Office website'
           WHEN substring(doc ->> 'source_url' FROM '^[A-Za-z]+://([^/:?#]+)')
                ~* 'halt' THEN 'HALT'
           ELSE 'Elsewhere'
       END AS source
FROM corpus.current_versions AS v,
     jsonb_array_elements(
         CASE WHEN jsonb_typeof(v.dataset_metadata -> 'documents') = 'array'
              THEN v.dataset_metadata -> 'documents' ELSE '[]'::jsonb END
     ) AS doc;

CREATE OR REPLACE VIEW evaluation.case_attributes AS
SELECT d.doc_id, a.library_id, f.group_label, f.label, f.field, f.value, d.dataset
FROM evaluation.document_library_ids AS d
JOIN evaluation.library_attributes AS a
    ON a.library_id = d.library_id
   AND a.snapshot_id = (SELECT snapshot_id FROM evaluation.library_latest)
JOIN evaluation.library_filters AS f
    ON f.snapshot_id = a.snapshot_id AND f.field = a.field AND f.value = a.value;

CREATE OR REPLACE VIEW evaluation.case_metadata AS
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
                  WHERE c.doc_id = d.doc_id AND c.dataset = d.dataset
                  GROUP BY c.group_label) AS g),
           '{}'::jsonb
       ) AS details,
       d.dataset,
       d.source
FROM evaluation.document_library_ids AS d
LEFT JOIN evaluation.library_reviews AS r
    ON r.library_id = d.library_id
   AND r.snapshot_id = (SELECT snapshot_id FROM evaluation.library_latest);
""",
    ),
    (
        "004_sections",
        """
-- The hierarchical representation: each review's blocks in reading order, grouped under
-- the sections their headings open. Filled by the group_sections script
-- (f/dhr/group_sections.py), one dataset version at a time. Reloading a version removes
-- its sections too (ON DELETE CASCADE), so it has to be grouped again.

-- Which dataset versions have been grouped, from which headings, and by which code.
CREATE TABLE corpus.grouped_versions (
    revision     text PRIMARY KEY REFERENCES corpus.dataset_versions ON DELETE CASCADE,
    -- 'checked': the headings the team validated by hand (DHRC-100);
    -- 'datalab': Datalab's own headings, unchecked (DHRC-RAW)
    headings     text NOT NULL CHECK (headings IN ('checked', 'datalab')),
    grouped_at   timestamptz NOT NULL DEFAULT now(),
    git_commit   text,                   -- the pipeline code that grouped it
    n_documents  integer NOT NULL,       -- reviews grouped
    n_sections   integer NOT NULL,
    n_blocks     integer NOT NULL,
    n_skipped    integer NOT NULL,       -- reviews left out: their headings didn't fit together
    skipped      jsonb NOT NULL DEFAULT '[]'  -- why each one was left out
);

-- One row per section: a heading and everything after it, up to the next heading at the
-- same level or above. Sections nest: parent_id is the section this one sits in.
CREATE TABLE corpus.sections (
    revision       text NOT NULL,
    doc_id         text NOT NULL,
    section_id     text NOT NULL,        -- its heading's block id, e.g. /page/5/SectionHeader/8
    parent_id      text,                 -- NULL for a top-level section
    level          integer NOT NULL CHECK (level >= 1),  -- 1 = top level
    heading        text NOT NULL,        -- the heading's words
    position       integer NOT NULL,     -- its heading's place in reading order
    last_position  integer NOT NULL,     -- the last block inside it, subsections included
    first_page     integer NOT NULL,     -- pages count from 0, as in the block ids
    last_page      integer NOT NULL,     -- more than first_page: it crosses a page break
    n_blocks       integer NOT NULL,     -- blocks directly in it (not its heading or subsections)
    PRIMARY KEY (revision, doc_id, section_id),
    UNIQUE (revision, doc_id, position),
    FOREIGN KEY (revision, doc_id) REFERENCES corpus.documents ON DELETE CASCADE,
    FOREIGN KEY (revision, doc_id, parent_id)
        REFERENCES corpus.sections (revision, doc_id, section_id) ON DELETE CASCADE,
    CHECK (last_position >= position AND last_page >= first_page)
);
CREATE INDEX ON corpus.sections (revision, doc_id, parent_id);

-- One row per block, in reading order, with the section it sits directly in. Page headers
-- and footers (running titles, "Page 3 of 56") are left out.
CREATE TABLE corpus.blocks (
    revision    text NOT NULL,
    doc_id      text NOT NULL,
    block_id    text NOT NULL,           -- the dataset's block id, e.g. /page/5/Text/3
    position    integer NOT NULL,        -- reading order within the review, from 0
    page        integer NOT NULL,
    block_type  text NOT NULL,           -- Text, ListGroup, Table, SectionHeader, ...
    section_id  text,                    -- a heading's is its own; NULL before the first heading
    html        text NOT NULL,
    text        text NOT NULL,           -- the words without HTML (for a picture: Datalab's
                                         -- description of it)
    PRIMARY KEY (revision, doc_id, block_id),
    UNIQUE (revision, doc_id, position),
    FOREIGN KEY (revision, doc_id) REFERENCES corpus.documents ON DELETE CASCADE,
    FOREIGN KEY (revision, doc_id, section_id) REFERENCES corpus.sections ON DELETE CASCADE
);

-- The newest version of each dataset, where it has been grouped. Pick with WHERE dataset = ...
CREATE VIEW corpus.current_sections AS
SELECT s.*, v.dataset, g.headings
FROM corpus.sections AS s
JOIN corpus.current_versions AS v USING (revision)
JOIN corpus.grouped_versions AS g USING (revision);

CREATE VIEW corpus.current_blocks AS
SELECT b.*, v.dataset
FROM corpus.blocks AS b
JOIN corpus.current_versions AS v USING (revision);

-- A section with everything nested inside it, in reading order: its heading, its own
-- blocks, and its subsections' headings and blocks. Each block comes with the level and
-- heading of the section it sits directly in. For example:
--   SELECT * FROM corpus.section_blocks('DHRC-100', 'Mary - Bromley - 2018_a - DHR',
--                                       '/page/5/SectionHeader/0');
CREATE FUNCTION corpus.section_blocks(dataset text, doc_id text, section_id text)
RETURNS TABLE ("position" integer, page integer, block_id text, block_type text,
               section_id text, level integer, heading text, text text, html text)
LANGUAGE sql STABLE
AS $fn$
    SELECT b.position, b.page, b.block_id, b.block_type, b.section_id,
           own.level, own.heading, b.text, b.html
    FROM corpus.current_sections AS s
    JOIN corpus.blocks AS b
      ON b.revision = s.revision AND b.doc_id = s.doc_id
     AND b.position BETWEEN s.position AND s.last_position
    JOIN corpus.sections AS own
      ON own.revision = b.revision AND own.doc_id = b.doc_id
     AND own.section_id = b.section_id
    WHERE s.dataset = section_blocks.dataset
      AND s.doc_id = section_blocks.doc_id
      AND s.section_id = section_blocks.section_id
    ORDER BY b.position
$fn$;

-- The same section as one piece of text, for reading or for an LLM prompt. Headings are
-- marked like Markdown: "# " for level 1, "## " for level 2, and so on.
CREATE FUNCTION corpus.section_text(dataset text, doc_id text, section_id text)
RETURNS text
LANGUAGE sql STABLE
AS $fn$
    SELECT string_agg(
               CASE WHEN b.block_type = 'SectionHeader'
                    THEN repeat('#', b.level) || ' ' || b.text ELSE b.text END,
               E'\\n\\n' ORDER BY b.position)
    FROM corpus.section_blocks(section_text.dataset, section_text.doc_id,
                               section_text.section_id) AS b
    WHERE b.text <> ''
$fn$;
""",
    ),
    (
        "005_templates",
        """
-- The report templates in the 2016 DHR statutory guidance (f/dhr/templates.py), and for
-- each review section the template sections it might be (its candidates), found by
-- f/dhr/find_template_candidates. An LLM picks among the candidates in the next O1 step.

-- The guidance document the templates were read from.
CREATE TABLE corpus.guidance_documents (
    guidance    text PRIMARY KEY,            -- e.g. 'dhr-2016'
    title       text NOT NULL,
    source_url  text NOT NULL,
    sha256      text NOT NULL,               -- of the PDF the templates were read from
    n_pages     integer NOT NULL,
    loaded_at   timestamptz NOT NULL DEFAULT now()
);

-- One row per template (one per appendix of the guidance).
CREATE TABLE corpus.templates (
    template    text PRIMARY KEY,            -- e.g. 'dhr-2016/overview-report'
    guidance    text NOT NULL REFERENCES corpus.guidance_documents ON DELETE CASCADE,
    appendix    integer NOT NULL,            -- its appendix number in the guidance
    title       text NOT NULL,               -- e.g. 'Overview report template'
    -- 'overview report', 'executive summary', 'individual management review', 'action plan'
    kind        text NOT NULL,
    first_page  integer NOT NULL,            -- page numbers as printed in the guidance
    last_page   integer NOT NULL
);

-- One row per section of a template, in the template's order.
CREATE TABLE corpus.template_sections (
    template    text NOT NULL REFERENCES corpus.templates ON DELETE CASCADE,
    position    integer NOT NULL,            -- from 1
    heading     text NOT NULL,               -- e.g. 'Timescales'
    printed     boolean NOT NULL,            -- false: no heading in the guidance (named by us)
    guidance    text NOT NULL,               -- what the template says goes there ('' if nothing)
    page        integer NOT NULL,            -- as printed in the guidance
    PRIMARY KEY (template, position)
);

-- Which grouped dataset versions have candidates, and how they were found.
CREATE TABLE corpus.candidate_versions (
    revision           text PRIMARY KEY REFERENCES corpus.grouped_versions ON DELETE CASCADE,
    guidance           text NOT NULL REFERENCES corpus.guidance_documents ON DELETE CASCADE,
    model              text NOT NULL,        -- the embedding model, e.g. 'BAAI/bge-small-en-v1.5'
    k                  integer NOT NULL,     -- how many candidates each method contributes
    min_heading_score  real NOT NULL,        -- below this, a heading match isn't a candidate
    matched_at         timestamptz NOT NULL DEFAULT now(),
    git_commit         text,
    n_sections         integer NOT NULL,
    n_candidates       integer NOT NULL
);

-- For each review section, the template sections it might be. A template section is a
-- candidate if its heading is among the k most like the section's heading (and at least
-- min_heading_score alike), or its text among the k most like the section's text. Both
-- scores are kept for every candidate, whichever way it was found.
CREATE TABLE corpus.template_candidates (
    revision           text NOT NULL,
    doc_id             text NOT NULL,
    section_id         text NOT NULL,
    template           text NOT NULL,
    template_position  integer NOT NULL,
    heading_score      real NOT NULL,        -- 0 to 100: how alike the two headings' words are
    heading_rank       integer NOT NULL,     -- 1 = the template section with the most alike heading
    text_score         real NOT NULL,        -- -1 to 1: cosine similarity of the embeddings
    text_rank          integer NOT NULL,     -- 1 = the template section with the most alike text
    PRIMARY KEY (revision, doc_id, section_id, template, template_position),
    FOREIGN KEY (revision, doc_id, section_id) REFERENCES corpus.sections ON DELETE CASCADE,
    FOREIGN KEY (template, template_position)
        REFERENCES corpus.template_sections ON DELETE CASCADE
);

-- The candidates for the newest grouped version of each dataset, with both sides' headings.
-- Pick with WHERE dataset = ...; e.g. the most likely by text for one review:
--   SELECT section_heading, template_heading, text_score FROM corpus.current_template_candidates
--   WHERE dataset = 'DHRC-100' AND doc_id = '...' AND text_rank = 1 ORDER BY section_position;
CREATE VIEW corpus.current_template_candidates AS
SELECT c.revision, v.dataset, c.doc_id, c.section_id,
       s.position AS section_position, s.level AS section_level, s.heading AS section_heading,
       c.template, t.title AS template_title, t.kind AS template_kind,
       c.template_position, ts.heading AS template_heading,
       c.heading_score, c.heading_rank, c.text_score, c.text_rank
FROM corpus.template_candidates AS c
JOIN corpus.current_versions AS v USING (revision)
JOIN corpus.sections AS s USING (revision, doc_id, section_id)
JOIN corpus.templates AS t ON t.template = c.template
JOIN corpus.template_sections AS ts
  ON ts.template = c.template AND ts.position = c.template_position;
""",
    ),
    (
        "006_candidates_per_guidance",
        """
-- A dataset version can have template candidates against more than one guidance (the 2016
-- DHR guidance and the 2026 DARDR guidance), so candidate_versions has a row for each.
ALTER TABLE corpus.candidate_versions DROP CONSTRAINT candidate_versions_pkey;
ALTER TABLE corpus.candidate_versions ADD PRIMARY KEY (revision, guidance);

-- The candidates view gains the guidance each template comes from. Pick with
-- WHERE dataset = ... AND guidance = ...
CREATE OR REPLACE VIEW corpus.current_template_candidates AS
SELECT c.revision, v.dataset, c.doc_id, c.section_id,
       s.position AS section_position, s.level AS section_level, s.heading AS section_heading,
       c.template, t.title AS template_title, t.kind AS template_kind,
       c.template_position, ts.heading AS template_heading,
       c.heading_score, c.heading_rank, c.text_score, c.text_rank,
       t.guidance
FROM corpus.template_candidates AS c
JOIN corpus.current_versions AS v USING (revision)
JOIN corpus.sections AS s USING (revision, doc_id, section_id)
JOIN corpus.templates AS t ON t.template = c.template
JOIN corpus.template_sections AS ts
  ON ts.template = c.template AND ts.position = c.template_position;
""",
    ),
    (
        "007_template_sources",
        """
-- A guidance can come in several files: the 2026 DARDR template (Annex A) is published as a
-- Word file of its own, and is read from that. Each template records the file it was read
-- from and its sha256; guidance_documents.sha256 is then a fingerprint of all its files
-- together. A Word file has no pages, so page numbers can be empty.
ALTER TABLE corpus.templates ADD COLUMN source_url text;
ALTER TABLE corpus.templates ADD COLUMN sha256 text;
ALTER TABLE corpus.templates ALTER COLUMN first_page DROP NOT NULL;
ALTER TABLE corpus.templates ALTER COLUMN last_page DROP NOT NULL;
ALTER TABLE corpus.template_sections ALTER COLUMN page DROP NOT NULL;
-- Templates stored before this came from their guidance's one file.
UPDATE corpus.templates AS t
SET source_url = g.source_url, sha256 = g.sha256
FROM corpus.guidance_documents AS g
WHERE g.guidance = t.guidance;
""",
    ),
    (
        "008_template_choices",
        """
-- O1 stage 5: each review mapped onto one statutory template. An LLM first decides which
-- template the whole review is written to (corpus.document_choices), then which section of
-- that template, or of the action plan that goes with it, each of its sections is
-- (corpus.template_choices), or none. Filled by
-- f/dhr/O1/choose_template_section/choose_template_section, which runs the LLM on Blythe.
-- Together with the sections, this is the standardised representation: every section mapped
-- to a section of its review's template, or flagged as unmatched.

-- One row per dataset version and guidance that has choices, and how they were made.
-- Choosing again for the same version and guidance replaces its choices.
CREATE TABLE corpus.choice_versions (
    revision        text NOT NULL REFERENCES corpus.grouped_versions ON DELETE CASCADE,
    guidance        text NOT NULL REFERENCES corpus.guidance_documents ON DELETE CASCADE,
    reviews         text NOT NULL,       -- 'all', or 'pilot' (the specification's 10 pilot reviews)
    model           text NOT NULL,       -- e.g. 'Qwen/Qwen3.8-27B-FP8'
    model_revision  text,                -- the model's Hugging Face commit
    prompt_version  text NOT NULL,       -- PROMPT_VERSION in choose_template_section/prompts.py
    prompts_sha256  jsonb NOT NULL,      -- of each prompts file sent to Blythe
    run_details     jsonb NOT NULL,      -- versions, GPU, timings and settings from the jobs
    slurm_job_ids   text[],              -- the document job, then the section job
    blythe_runs     text[],              -- their run folders: ~/narratrace/runs/<this>
    chosen_at       timestamptz NOT NULL DEFAULT now(),
    git_commit      text,
    n_reviews       integer NOT NULL,
    n_reviews_matched integer NOT NULL,  -- reviews given a template
    n_sections      integer NOT NULL,
    n_matched       integer NOT NULL,    -- sections given a template section
    n_unmatched     integer NOT NULL,    -- "none of these", or in a review that matched no template
    n_no_answer     integer NOT NULL,    -- no usable answer (see the error columns)
    PRIMARY KEY (revision, guidance)
);

-- One row per review and guidance: the template the whole review is written to. template is
-- NULL when it matched none (answer 'Z') or there was no usable answer (answer NULL).
CREATE TABLE corpus.document_choices (
    revision       text NOT NULL,
    doc_id         text NOT NULL,
    guidance       text NOT NULL,
    template       text REFERENCES corpus.templates ON DELETE CASCADE,
    answer         text,                 -- the model's letter; 'Z' = none of these
    probability    real,                 -- the model's probability for its answer, 0 to 1
    -- its probability for each option it gave any: {"<template>": p, "none": p}
    probabilities  jsonb NOT NULL DEFAULT '{}',
    error          text,                 -- why there is no usable answer
    PRIMARY KEY (revision, doc_id, guidance),
    FOREIGN KEY (revision, guidance) REFERENCES corpus.choice_versions ON DELETE CASCADE,
    FOREIGN KEY (revision, doc_id) REFERENCES corpus.documents ON DELETE CASCADE
);

-- One row per review section and guidance. template is NULL when the section matched none
-- of its options (answer 'Z'), its review matched no template, or there was no usable answer.
CREATE TABLE corpus.template_choices (
    revision           text NOT NULL,
    doc_id             text NOT NULL,
    section_id         text NOT NULL,
    guidance           text NOT NULL,
    template           text,
    template_position  integer,
    answer             text,             -- the model's letter; 'Z' = none of these
    probability        real,             -- the model's probability for its answer, 0 to 1
    none_probability   real,             -- its probability for "none of these"
    -- its probability for each option it gave any: {"<template>#<position>": p, "none": p}
    probabilities      jsonb NOT NULL DEFAULT '{}',
    n_options          integer NOT NULL, -- how many template sections it chose from
    error              text,             -- why there is no usable answer
    PRIMARY KEY (revision, doc_id, section_id, guidance),
    FOREIGN KEY (revision, doc_id, guidance)
        REFERENCES corpus.document_choices ON DELETE CASCADE,
    FOREIGN KEY (revision, doc_id, section_id) REFERENCES corpus.sections ON DELETE CASCADE,
    FOREIGN KEY (template, template_position)
        REFERENCES corpus.template_sections ON DELETE CASCADE,
    CHECK ((template IS NULL) = (template_position IS NULL))
);

-- Each review's template, for the newest grouped version of each dataset. Pick with
-- WHERE dataset = ... AND guidance = ...
CREATE VIEW corpus.current_document_choices AS
SELECT d.revision, v.dataset, d.guidance, d.doc_id,
       CASE WHEN d.template IS NOT NULL THEN 'matched'
            WHEN d.answer IS NOT NULL THEN 'unmatched'
            ELSE 'no answer' END AS status,
       d.template, t.title AS template_title, t.kind AS template_kind,
       d.answer, d.probability, d.probabilities, d.error,
       cv.model, cv.prompt_version, cv.reviews
FROM corpus.document_choices AS d
JOIN corpus.choice_versions AS cv USING (revision, guidance)
JOIN corpus.current_versions AS v USING (revision)
LEFT JOIN corpus.templates AS t ON t.template = d.template;

-- Each section with the template section it was mapped to. status is 'matched', 'unmatched'
-- (none of its options, or its review matched no template) or 'no answer'. For example,
-- every section of one review:
--   SELECT section_heading, status, template_title, template_heading, probability
--   FROM corpus.current_template_choices
--   WHERE dataset = 'DHRC-100' AND guidance = 'dhr-2016' AND doc_id = '...'
--   ORDER BY section_position;
CREATE VIEW corpus.current_template_choices AS
SELECT c.revision, v.dataset, c.guidance, c.doc_id, c.section_id,
       s.position AS section_position, s.level AS section_level, s.heading AS section_heading,
       CASE WHEN c.template IS NOT NULL THEN 'matched'
            WHEN c.answer IS NOT NULL OR d.answer = 'Z' THEN 'unmatched'
            ELSE 'no answer' END AS status,
       d.template AS document_template, dt.title AS document_template_title,
       c.template, t.title AS template_title, t.kind AS template_kind,
       c.template_position, ts.heading AS template_heading,
       c.answer, c.probability, c.none_probability, c.probabilities, c.n_options, c.error,
       cv.model, cv.prompt_version, cv.reviews
FROM corpus.template_choices AS c
JOIN corpus.document_choices AS d USING (revision, doc_id, guidance)
JOIN corpus.choice_versions AS cv USING (revision, guidance)
JOIN corpus.current_versions AS v USING (revision)
JOIN corpus.sections AS s USING (revision, doc_id, section_id)
LEFT JOIN corpus.templates AS dt ON dt.template = d.template
LEFT JOIN corpus.templates AS t ON t.template = c.template
LEFT JOIN corpus.template_sections AS ts
  ON ts.template = c.template AND ts.position = c.template_position;

-- The standardised representation, one row per part of each review: every section of its
-- template, then of the action plan, in the guidance's order, each with the review's sections
-- mapped to it in their original order (none if the review has no such section; several
-- sections can share one, a timeline and a timeline analysis say); then the sections that
-- matched none (status 'unmatched') and any without an answer ('no answer'). Only the
-- mapping is here: the sections' text is unchanged in corpus.blocks. For example:
--   SELECT template_heading, section_headings FROM corpus.current_standardised
--   WHERE dataset = 'DHRC-100' AND guidance = 'dhr-2016' AND doc_id = '...' ORDER BY part;
CREATE VIEW corpus.current_standardised AS
WITH parts AS (
    SELECT d.revision, d.doc_id, d.guidance, ts.template, ts.position, ts.heading,
           t.title,
           row_number() OVER (PARTITION BY d.revision, d.doc_id, d.guidance
                              ORDER BY ts.template <> d.template, t.appendix, ts.position) AS part
    FROM corpus.document_choices AS d
    JOIN corpus.templates AS dt ON dt.template = d.template
    JOIN corpus.templates AS t ON t.guidance = d.guidance
     AND (t.template = d.template OR (t.kind = 'action plan' AND dt.kind <> 'action plan'))
    JOIN corpus.template_sections AS ts ON ts.template = t.template
), mapped AS (
    SELECT revision, doc_id, guidance, template, template_position, status,
           array_agg(section_id ORDER BY section_position) AS section_ids,
           array_agg(section_heading ORDER BY section_position) AS section_headings,
           min(probability) AS min_probability
    FROM corpus.current_template_choices
    GROUP BY revision, doc_id, guidance, template, template_position, status
)
SELECT p.revision, v.dataset, p.guidance, p.doc_id, p.part, 'template section' AS status,
       p.template, p.title AS template_title, p.position AS template_position,
       p.heading AS template_heading, coalesce(cardinality(m.section_ids), 0) AS n_sections,
       coalesce(m.section_ids, '{}') AS section_ids,
       coalesce(m.section_headings, '{}') AS section_headings, m.min_probability
FROM parts AS p
JOIN corpus.current_versions AS v USING (revision)
LEFT JOIN mapped AS m
  ON m.revision = p.revision AND m.doc_id = p.doc_id AND m.guidance = p.guidance
 AND m.template = p.template AND m.template_position = p.position
UNION ALL
SELECT m.revision, v.dataset, m.guidance, m.doc_id,
       CASE m.status WHEN 'unmatched' THEN 100000 ELSE 100001 END, m.status,
       NULL, NULL, NULL, NULL, cardinality(m.section_ids), m.section_ids, m.section_headings,
       m.min_probability
FROM mapped AS m
JOIN corpus.current_versions AS v USING (revision)
WHERE m.status <> 'matched';
""",
    ),
    (
        "009_doubtful_choices",
        """
-- O1 stage 5: doubtful answers are marked, and a review the model wasn't sure matched no
-- template falls back to its likeliest template (Rowan, 19 Nov 2026).
--   * document_choices.fallback: the model's answer was "none of these" but with less
--     probability than the fallback threshold, so the review was given its likeliest
--     template anyway, and its sections were mapped onto it.
--   * doubtful (in the views): the model gave its answer less than 50%, the review's
--     template is a fallback, or (for a section) its review's template is doubtful. These
--     are the answers to check by hand.
ALTER TABLE corpus.document_choices ADD COLUMN fallback boolean NOT NULL DEFAULT false;
ALTER TABLE corpus.choice_versions ADD COLUMN n_reviews_doubtful integer NOT NULL DEFAULT 0;
ALTER TABLE corpus.choice_versions ADD COLUMN n_doubtful integer NOT NULL DEFAULT 0;

CREATE OR REPLACE VIEW corpus.current_document_choices AS
SELECT d.revision, v.dataset, d.guidance, d.doc_id,
       CASE WHEN d.template IS NOT NULL THEN 'matched'
            WHEN d.answer IS NOT NULL THEN 'unmatched'
            ELSE 'no answer' END AS status,
       d.template, t.title AS template_title, t.kind AS template_kind,
       d.answer, d.probability, d.probabilities, d.error,
       cv.model, cv.prompt_version, cv.reviews,
       d.fallback,
       d.template IS NOT NULL AND (d.fallback OR d.probability < 0.5) AS doubtful
FROM corpus.document_choices AS d
JOIN corpus.choice_versions AS cv USING (revision, guidance)
JOIN corpus.current_versions AS v USING (revision)
LEFT JOIN corpus.templates AS t ON t.template = d.template;

CREATE OR REPLACE VIEW corpus.current_template_choices AS
SELECT c.revision, v.dataset, c.guidance, c.doc_id, c.section_id,
       s.position AS section_position, s.level AS section_level, s.heading AS section_heading,
       CASE WHEN c.template IS NOT NULL THEN 'matched'
            WHEN c.answer IS NOT NULL OR d.answer = 'Z' THEN 'unmatched'
            ELSE 'no answer' END AS status,
       d.template AS document_template, dt.title AS document_template_title,
       c.template, t.title AS template_title, t.kind AS template_kind,
       c.template_position, ts.heading AS template_heading,
       c.answer, c.probability, c.none_probability, c.probabilities, c.n_options, c.error,
       cv.model, cv.prompt_version, cv.reviews,
       c.template IS NOT NULL
           AND (c.probability < 0.5 OR d.fallback OR d.probability < 0.5) AS doubtful
FROM corpus.template_choices AS c
JOIN corpus.document_choices AS d USING (revision, doc_id, guidance)
JOIN corpus.choice_versions AS cv USING (revision, guidance)
JOIN corpus.current_versions AS v USING (revision)
JOIN corpus.sections AS s USING (revision, doc_id, section_id)
LEFT JOIN corpus.templates AS dt ON dt.template = d.template
LEFT JOIN corpus.templates AS t ON t.template = c.template
LEFT JOIN corpus.template_sections AS ts
  ON ts.template = c.template AND ts.position = c.template_position;
""",
    ),
]
