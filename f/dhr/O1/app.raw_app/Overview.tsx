import React from 'react'
import { GUIDANCES, HEADINGS, Status, formatNumber, shortenId, formatDateTime } from './shared'

type Props = { status: Status | null; goTo: (tab: string) => void }

// The steps of Stage 1, as in the specification's data flow diagram (Appendix B).
type Step = {
  title: string
  what: string
  built: boolean
  now?: (status: Status) => React.ReactNode
  run?: boolean
  see?: { tab: string; label: string } // the tab that shows what the step produced
}

const STEPS: Step[] = [
  {
    title: 'Load the reviews',
    what:
      'Import the reviews from Hugging Face and verify the files against the dataset checksums. ' +
      'Keep the original page-based JSON, with the dataset name and version recorded for each review.',
    built: true,
    run: true,
    now: (status) => (
      <ul className="facts-list">
        {status.datasets.map((dataset) => (
          <li key={dataset.name}>
            <strong>{dataset.name}:</strong>{' '}
            {dataset.version
              ? `${formatNumber(dataset.version.n_reviews)} reviews, version ${shortenId(dataset.version.revision)}, loaded ${formatDateTime(dataset.version.loaded_at)}`
              : 'not loaded yet'}
          </li>
        ))}
      </ul>
    ),
  },
  {
    title: 'Link the case metadata',
    what:
      'Link reviews to the characteristics recorded in the Home Office DHR Library, such as age ' +
      'and relationship. These details are stored separately for evaluation and are excluded from ' +
      'the wiki agent’s input.',
    built: true,
    run: true,
    now: (status) =>
      status.case_metadata ? (
        <p>
          Snapshot {status.case_metadata.snapshot_id}, collected{' '}
          {formatDateTime(status.case_metadata.collected_at)}:{' '}
          {formatNumber(status.case_metadata.n_reviews)} library reviews,{' '}
          {formatNumber(status.case_metadata.n_details)} case details.
        </p>
      ) : (
        <p>Not collected yet.</p>
      ),
  },
  {
    title: 'Group blocks by section',
    what:
      'Rebuild each review’s section structure from its headings. Group text across page breaks ' +
      'so a section can be read with its subsections and traced back to the original pages.',
    built: true,
    run: true,
    see: { tab: 'sections', label: 'See the sections' },
    now: (status) => (
      <ul className="facts-list">
        {status.datasets.map((dataset) => (
          <li key={dataset.name}>
            <strong>{dataset.name}:</strong>{' '}
            {dataset.sections
              ? `${formatNumber(dataset.sections.n_sections)} sections from ${formatNumber(dataset.sections.n_reviews)} reviews ` +
                `(${formatNumber(dataset.sections.n_across_pages)} cross a page break), from ${HEADINGS[dataset.sections.headings]}, ` +
                `grouped ${formatDateTime(dataset.sections.grouped_at)}` +
                (dataset.sections.n_skipped
                  ? `; ${formatNumber(dataset.sections.n_skipped)} reviews left out`
                  : '')
              : dataset.version
                ? 'loaded, not grouped yet'
                : 'not loaded yet'}
          </li>
        ))}
      </ul>
    ),
  },
  {
    title: 'Find candidate template sections',
    what:
      'Suggest where each review section fits in a standard report template, using heading and ' +
      'text similarity. The app supports the overview report, executive summary, individual ' +
      'management review and action plan templates from the 2016 guidance. Templates from the ' +
      '2026 guidance are also available for comparison.',
    built: true,
    run: true,
    see: { tab: 'candidates', label: 'See the candidates' },
    now: (status) => (
      <ul className="facts-list">
        {status.templates.map((templateSummary) => (
          <li key={templateSummary.guidance}>
            <strong>{GUIDANCES[templateSummary.guidance] ?? templateSummary.guidance}:</strong>{' '}
            {formatNumber(templateSummary.n_templates)} templates,{' '}
            {formatNumber(templateSummary.n_sections)} sections, read{' '}
            {formatDateTime(templateSummary.loaded_at)}
          </li>
        ))}
        {status.datasets.map((dataset) => (
          <li key={dataset.name}>
            <strong>{dataset.name}:</strong>{' '}
            {dataset.candidates.length
              ? dataset.candidates
                  .map(
                    (c) =>
                      `${formatNumber(c.n_candidates)} candidates for ${formatNumber(c.n_sections)} sections against the ` +
                      `${GUIDANCES[c.guidance] ?? c.guidance} (${formatNumber(c.n_with_heading_match)} with a heading ` +
                      `match), found ${formatDateTime(c.matched_at)}`,
                  )
                  .join('; ')
              : dataset.sections
                ? 'grouped, no candidates yet'
                : 'not grouped yet'}
          </li>
        ))}
      </ul>
    ),
  },
  {
    title: 'Choose the template section',
    what:
      'The planned final step will use an LLM to select a template section from the candidates. ' +
      'Sections without a suitable match will be retained and marked as unmatched. This will ' +
      'allow the same type of material to be found consistently across reviews.',
    built: false,
  },
]

// Which steps are built, in order (the app's sidebar shows this as a meter).
export const STEP_BUILT = STEPS.map((step) => step.built)

const REPRESENTATIONS = [
  {
    name: 'Page-based',
    what: 'The original extracted text and layout, page by page.',
    built: true,
  },
  { name: 'Hierarchical', what: 'Text organised into sections and subsections.', built: true },
  { name: 'Standardised', what: 'Sections mapped to the 2016 template.', built: false },
]

export default function Overview({ status, goTo }: Props) {
  const next = STEPS.findIndex((step) => !step.built)
  return (
    <div className="page">
      <header className="overview-head">
        <p className="aim">
          <strong>O1.</strong> Prepare the reviews for analysis: rebuild their section structure,
          map sections to a common template and link each review to its case metadata.
        </p>
        <p className="lead">
          This app shows the data behind NarraTrace. Browse the reviews, inspect their sections and
          compare suggested template matches. Four of the five processing steps are available; the
          final LLM selection step is still to be built. The resulting database will supply the LLM
          wiki in Stage 2.
        </p>
      </header>

      <ol className="step-strip" aria-label="The five steps">
        {STEPS.map((step, index) => (
          <li key={step.title} className={step.built ? 'built' : index === next ? 'next' : ''}>
            {/* A button, not a #link: the app runs inside Windmill's frame, which owns the URL. */}
            <button
              onClick={() =>
                document
                  .getElementById(`step-${index + 1}`)
                  ?.scrollIntoView({ behavior: 'smooth', block: 'start' })
              }
            >
              <span className="strip-n">{index + 1}</span>
              <span className="strip-title">{step.title}</span>
              <span className="strip-state">
                {step.built ? 'Built' : index === next ? 'Next' : 'Not built yet'}
              </span>
            </button>
          </li>
        ))}
      </ol>

      <ul className="representations" aria-label="The three representations">
        {REPRESENTATIONS.map((r) => (
          <li key={r.name} className={r.built ? 'built' : ''}>
            <strong>{r.name}</strong>
            <span>{r.what}</span>
            <span className="chip-line">{r.built ? 'In the database' : 'From step 5'}</span>
          </li>
        ))}
        <li className="built">
          <strong>Case metadata</strong>
          <span>Characteristics recorded for each review in the DHR Library.</span>
          <span className="chip-line">In the database</span>
        </li>
      </ul>

      <section>
        <h2>How the reviews are prepared</h2>
        <p className="muted">
          Follow the process from source files to comparable sections. Available steps can be
          started from the <a onClick={() => goTo('run')}>Run</a> view.
        </p>
        <ol className="pipeline">
          {STEPS.map((step, index) => (
            <li
              key={step.title}
              id={`step-${index + 1}`}
              className={step.built ? 'step built' : 'step planned'}
            >
              <div className="step-number">{index + 1}</div>
              <div className="step-body">
                <div className="step-head">
                  <h3>{step.title}</h3>
                  <span className={step.built ? 'chip done' : 'chip planned'}>
                    {step.built ? 'Built' : 'Not built yet'}
                  </span>
                </div>
                <p>{step.what}</p>
                {step.built && status && step.now && (
                  <div className="step-now">{step.now(status)}</div>
                )}
                {(step.see || step.run) && (
                  <div className="step-actions">
                    {step.see && (
                      <button className="link-button" onClick={() => goTo(step.see!.tab)}>
                        {step.see.label}
                      </button>
                    )}
                    {step.run && (
                      <button className="link-button" onClick={() => goTo('run')}>
                        Open run controls
                      </button>
                    )}
                  </div>
                )}
              </div>
            </li>
          ))}
        </ol>
      </section>

      <section>
        <h2>Development workflow</h2>
        <p>
          The app and processing scripts are maintained in NarraTrace-4YP/4yp_platform on GitHub.
          The team can make changes in Windmill or through a GitHub pull request.
        </p>
        <ul>
          <li>
            <strong>Windmill edits</strong> take effect immediately and are collected into a pull
            request for review. GitHub deployment waits while those changes are awaiting merge.
          </li>
          <li>
            <strong>GitHub changes</strong> are reviewed in a pull request and deployed to Windmill
            after merging.
          </li>
        </ul>
      </section>
    </div>
  )
}
