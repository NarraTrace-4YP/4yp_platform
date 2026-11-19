import React from 'react'
import {
  DatasetStatus,
  HEADINGS,
  Status,
  formatNumber,
  formatPercent,
  shortenId,
  formatDateTime,
} from './shared'

type Props = { status: Status | null; error: string | null }

const ABOUT: Record<string, string> = {
  'DHRC-100':
    'A collection of 100 reviews with manually checked headings and section structure, and ' +
    'corrections to most extraction errors. This is the main dataset for the project.',
  'DHRC-RAW':
    'A larger collection of 702 reviews extracted automatically with Datalab, without manual ' +
    'correction. It includes the 100 reviews in DHRC-100, allowing comparison of checked and raw ' +
    'extractions. The remaining reviews can test how well the pipeline handles uncorrected data.',
}

function SourceBar({ coverage }: { coverage: DatasetStatus['coverage'] }) {
  const parts = [
    { label: 'Home Office website', n: coverage.n_home_office, className: 'home-office' },
    { label: 'HALT', n: coverage.n_halt, className: 'halt' },
    { label: 'Elsewhere', n: coverage.n_elsewhere, className: 'elsewhere' },
    { label: 'No link', n: coverage.n_no_link, className: 'none' },
  ].filter((p) => p.n > 0)
  const total = coverage.n_listed
  if (!total) return null
  return (
    <div>
      <div className="bar" role="img" aria-label="Where the reviews were published">
        {parts.map((p) => (
          <span
            key={p.label}
            className={p.className}
            style={{ width: `${(100 * p.n) / total}%` }}
          />
        ))}
      </div>
      <ul className="legend">
        {parts.map((p) => (
          <li key={p.label}>
            <span className={`swatch ${p.className}`} /> {p.label}: {formatNumber(p.n)} (
            {formatPercent(p.n, total)})
          </li>
        ))}
      </ul>
    </div>
  )
}

function DatasetCard({ dataset }: { dataset: DatasetStatus }) {
  const v = dataset.version
  const c = dataset.coverage
  const s = dataset.sections
  const skipped = c.n_listed - c.n_loaded
  return (
    <article className="card">
      <header className="card-head">
        <h3>{dataset.name}</h3>
        <a
          href={`https://huggingface.co/datasets/${dataset.repo_id}`}
          target="_blank"
          rel="noreferrer"
        >
          {dataset.repo_id} ↗
        </a>
      </header>
      <p>{ABOUT[dataset.name]}</p>
      {!v ? (
        <p className="warning">Not loaded yet.</p>
      ) : (
        <>
          <dl className="facts">
            <dt>Version loaded</dt>
            <dd>
              <code>{shortenId(v.revision)}</code>, on {formatDateTime(v.loaded_at)}
              {v.git_commit && v.git_commit !== 'local' && (
                <span className="muted"> (by pipeline code {v.git_commit.slice(0, 7)})</span>
              )}
            </dd>
            <dt>Reviews stored</dt>
            <dd>
              {formatNumber(v.n_reviews)} of {formatNumber(dataset.expected_reviews)} expected
              {skipped > 0 && (
                <span className="muted"> · {formatNumber(skipped)} skipped (couldn’t be read)</span>
              )}
            </dd>
            <dt>Size</dt>
            <dd>
              {formatNumber(v.n_pages)} pages, {formatNumber(v.n_blocks)} blocks
            </dd>
            <dt>Checks</dt>
            <dd>
              Every file matched the dataset’s checksums.{' '}
              {dataset.strict
                ? 'Every review had to read as a document, or nothing was stored.'
                : 'Reviews that don’t read as a document are skipped and listed in the load’s log.'}
            </dd>
            <dt>Sections</dt>
            <dd>
              {s ? (
                <>
                  {formatNumber(s.n_sections)} sections from {formatNumber(s.n_reviews)} reviews (
                  {formatNumber(s.n_across_pages)} cross a page break), from {HEADINGS[s.headings]};
                  grouped {formatDateTime(s.grouped_at)}
                  {s.n_skipped > 0 && (
                    <span className="muted">
                      {' '}
                      · {formatNumber(s.n_skipped)} left out (their headings don’t fit together)
                    </span>
                  )}
                </>
              ) : (
                <span className="warning">Not grouped yet.</span>
              )}
            </dd>
          </dl>
          <h4>Where its reviews were published</h4>
          <SourceBar coverage={c} />
          <h4>Case details from the DHR Library</h4>
          <p>
            {formatNumber(c.n_in_library)} of {formatNumber(c.n_listed)} reviews are in the library
            ({formatPercent(c.n_in_library, c.n_listed)}), and {formatNumber(c.n_with_details)} have
            at least one case detail.
          </p>
        </>
      )}
    </article>
  )
}

export default function DataTab({ status, error }: Props) {
  if (error) return <div className="message error">Couldn’t read the status: {error}</div>
  if (!status) return <div className="message">Loading…</div>
  const snapshot = status.case_metadata
  return (
    <div className="page">
      <section>
        <h2>The datasets</h2>
        <p className="muted">
          DHRC-100 provides checked source material; DHRC-RAW provides a larger, uncorrected
          collection. They are stored separately so their results can be compared.
        </p>
        <div className="cards">
          {status.datasets.map((d) => (
            <DatasetCard key={d.name} dataset={d} />
          ))}
        </div>
      </section>

      <section>
        <h2>The case metadata</h2>
        <article className="card">
          {!snapshot ? (
            <p className="warning">Not collected yet.</p>
          ) : (
            <dl className="facts">
              <dt>Snapshot</dt>
              <dd>
                {snapshot.snapshot_id}, collected {formatDateTime(snapshot.collected_at)}
              </dd>
              <dt>Library</dt>
              <dd>
                {formatNumber(snapshot.n_reviews)} reviews listed,{' '}
                {formatNumber(snapshot.n_reviews_with_details)} with at least one case detail
              </dd>
              <dt>Searched</dt>
              <dd>
                {formatNumber(snapshot.n_filters)} filter options, giving{' '}
                {formatNumber(snapshot.n_details)} case details
              </dd>
            </dl>
          )}
          <p>
            Case characteristics are collected through the library’s search filters. A recorded
            match means the review appeared under that filter. A missing match does not distinguish
            an absent characteristic from an unrecorded one. The library’s manually checked data may
            still contain errors. Each snapshot covers the full library and can be linked to both
            datasets.
          </p>
        </article>
      </section>

      <section>
        <h2>What the labels mean</h2>
        <dl className="guide">
          <dt>Source</dt>
          <dd>
            Where a review was published, from its link in the dataset’s own list:{' '}
            <strong>Home Office website</strong> (the DHR Library), <strong>HALT</strong> (the HALT
            study’s repository, domestichomicide-halt.co.uk) or <strong>Elsewhere</strong> (usually
            a council’s website). Only reviews in the library can have case details.
          </dd>
          <dt>Loaded</dt>
          <dd>
            Whether the review’s file was stored. A DHRC-RAW review whose file doesn’t read as a
            document (no pages, wrong shape) is skipped, so its text isn’t in the database, but it
            still appears in the lists, marked “skipped when loading”.
          </dd>
          <dt>Version</dt>
          <dd>
            The Hugging Face commit a dataset was loaded from. Loading a newer version doesn’t
            delete the old one; the “current” views always show the newest of each dataset.
          </dd>
          <dt>Headings</dt>
          <dd>
            Where a dataset’s sections come from. <strong>Checked</strong> (DHRC-100): the team
            validated every heading’s level and parent, and which heading each block belongs to.{' '}
            <strong>Datalab</strong> (DHRC-RAW): Datalab’s own guesses, unchecked, so some sections
            will be wrong (page banners taken for headings, for example).
          </dd>
          <dt>Section</dt>
          <dd>
            A heading and everything after it up to the next heading at the same level or above, so
            it includes its subsections. Blocks before the first heading (title page, contents) are
            in no section. Page headers and footers are left out.
          </dd>
        </dl>
      </section>

      <section>
        <h2>Finding it in the database</h2>
        <table className="tables">
          <thead>
            <tr>
              <th>Table or view</th>
              <th>What’s in it</th>
              <th>Wiki agent?</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>
                <code>corpus.current_documents</code>
              </td>
              <td>
                Every review of the newest version of each dataset, with its original JSON in{' '}
                <code>raw</code>. Add <code>WHERE dataset = 'DHRC-100'</code> to pick one.
              </td>
              <td>Can read</td>
            </tr>
            <tr>
              <td>
                <code>corpus.dataset_versions</code>
              </td>
              <td>Every version loaded: which dataset, when, and by which pipeline code.</td>
              <td>Can read</td>
            </tr>
            <tr>
              <td>
                <code>corpus.current_sections</code>
              </td>
              <td>
                Every section: its heading, level, parent (<code>parent_id</code>), the pages it
                covers, and where it starts and ends in reading order (<code>position</code> to{' '}
                <code>last_position</code>).
              </td>
              <td>Can read</td>
            </tr>
            <tr>
              <td>
                <code>corpus.current_blocks</code>
              </td>
              <td>
                Every block in reading order, with its section, page, HTML and plain{' '}
                <code>text</code>. A block’s id (e.g. <code>/page/5/Text/3</code>) is the same as in
                the dataset, so it can be cited.
              </td>
              <td>Can read</td>
            </tr>
            <tr>
              <td>
                <code>corpus.section_blocks(dataset, doc_id, section_id)</code>
              </td>
              <td>
                A section with everything nested inside it, block by block.{' '}
                <code>corpus.section_text(…)</code> gives the same as one piece of text, with
                headings marked <code>#</code>, <code>##</code>…
              </td>
              <td>Can use</td>
            </tr>
            <tr>
              <td>
                <code>corpus.grouped_versions</code>
              </td>
              <td>
                Every grouping: which version, from which headings, when, by which code, and any
                reviews left out.
              </td>
              <td>Can read</td>
            </tr>
            <tr>
              <td>
                <code>corpus.template_sections</code>
              </td>
              <td>
                Each guidance’s templates (five from the 2016 DHR guidance, four from the 2026 DARDR
                guidance), section by section: heading, what the guidance says goes there, and page.{' '}
                <code>corpus.templates</code> lists the templates, each with the GOV.UK file it was
                read from and that file’s checksum (the 2026 DARDR template comes from its own Word
                file, the rest from the guidance PDFs).
              </td>
              <td>Can read</td>
            </tr>
            <tr>
              <td>
                <code>corpus.current_template_candidates</code>
              </td>
              <td>
                For each section, the template sections it might be, with how alike the headings are
                (<code>heading_score</code>, 0 to 100) and the text is (<code>text_score</code>, -1
                to 1), and the rank of each. <code>guidance</code> says whose templates; pick one
                with <code>WHERE guidance = 'dhr-2016'</code>.
              </td>
              <td>Can read</td>
            </tr>
            <tr>
              <td>
                <code>evaluation.case_metadata</code>
              </td>
              <td>
                One row per review per dataset: what the library lists, every case detail as JSON,
                and its source.
              </td>
              <td>Never</td>
            </tr>
            <tr>
              <td>
                <code>evaluation.case_attributes</code>
              </td>
              <td>One row per case detail per review, for counting and filtering.</td>
              <td>Never</td>
            </tr>
            <tr>
              <td>
                <code>evaluation.document_library_ids</code>
              </td>
              <td>Each review’s library link, source and whether it loaded.</td>
              <td>Never</td>
            </tr>
          </tbody>
        </table>
        <p className="muted">
          Examples:{' '}
          <code>SELECT dataset, source, count(*) FROM evaluation.case_metadata GROUP BY 1, 2;</code>
          <br />
          <code>
            SELECT corpus.section_text('DHRC-100', 'Mary - Bromley - 2018_a - DHR',
            '/page/5/SectionHeader/0');
          </code>
        </p>
      </section>
    </div>
  )
}
