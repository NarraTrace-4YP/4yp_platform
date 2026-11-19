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
      'Download each dataset from Hugging Face, check every file against its checksums, and store ' +
      'every review untouched (the page-based JSON Datalab produced), labelled with its dataset ' +
      'and version.',
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
      'Ask the Home Office DHR Library which reviews match each of its search filters (victim’s ' +
      'age, method of killing, relationship, …) and link the answers to our reviews. Kept in the ' +
      'evaluation schema, which the wiki agent can never read.',
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
      'Use the validated headings and their parents to put each block under its section, joining ' +
      'sections that cross a page break. This gives the hierarchical representation: a section ' +
      'can be fetched with everything nested inside it.',
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
      'For each section, find the template sections it might be, by matching headings (exactly or ' +
      'fuzzily) and by meaning (embeddings). The templates are read from the 2016 statutory guidance: ' +
      'the overview report (Appendix D in the specification), and also the executive summary, the ' +
      'agencies’ own reports (IMRs) and the action plan, since reviews follow different ones. The ' +
      'September 2026 guidance’s templates (DARDRs) can be matched too, for comparison.',
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
      'An LLM reads the section and picks the right template section from the candidates. Sections ' +
      'with no suitable match are kept and flagged as unmatched. This gives the standardised ' +
      'representation.',
    built: false,
  },
]

// Which steps are built, in order (the app's sidebar shows this as a meter).
export const STEP_BUILT = STEPS.map((step) => step.built)

const REPRESENTATIONS = [
  { name: 'Page-based', what: 'Each review as Datalab produced it, page by page.', built: true },
  { name: 'Hierarchical', what: 'Blocks grouped under their sections.', built: true },
  { name: 'Standardised', what: 'Sections mapped to the 2016 template.', built: false },
]

export default function Overview({ status, goTo }: Props) {
  const next = STEPS.findIndex((step) => !step.built)
  return (
    <div className="page">
      <header className="overview-head">
        <p className="aim">
          <strong>O1.</strong> Derive a hierarchical, standardised representation of DHRC-100.
          Organise the DHRC-100 corpus by document hierarchy and standardise against the 2016
          statutory guidance template (Appendix D). Store the result in a database linked to the
          Home Office DHR Library case metadata.
        </p>
        <p className="lead">
          In other words: take the reviews as Datalab extracted them (page by page), rebuild each
          one’s structure (which paragraphs belong to which section), line every section up with the
          sections the 2016 guidance says an overview report should have, and keep all of it in one
          database next to what the Home Office’s library records about each case. Stage 2 (the LLM
          wiki) is built from this database; the case metadata is kept back to check the wiki’s
          answers later.
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
          <span>Each review linked to what the DHR Library records about it.</span>
          <span className="chip-line">In the database</span>
        </li>
      </ul>

      <section>
        <h2>The steps</h2>
        <p className="muted">
          The steps in the specification’s Stage 1 diagram (Appendix B). The built ones can be run
          from the <a onClick={() => goTo('run')}>Run</a> view.
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
                        Run it
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
        <h2>Changing the code, including this app</h2>
        <p>
          The team code lives in GitHub (NarraTrace-4YP/4yp_platform), and you can change it in
          either place:
        </p>
        <ul>
          <li>
            <strong>In Windmill’s web editor.</strong> Your edit runs straight away. Within about 15
            minutes GitHub opens a pull request called “Changes made in Windmill” with it, for
            someone to review and merge. Until it’s merged, deploys from GitHub wait rather than
            overwrite your edit.
          </li>
          <li>
            <strong>On GitHub,</strong> with a branch and a pull request. Merging it deploys it here
            within a couple of minutes.
          </li>
        </ul>
      </section>
    </div>
  )
}
