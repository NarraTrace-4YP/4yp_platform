import React from 'react'
import { GUIDANCES, HEADINGS, Status, number, shortId, when } from './shared'

type Props = { status: Status | null; goTo: (tab: string) => void }

// The steps of Stage 1, as in the specification's data flow diagram (Appendix B).
type Step = {
  title: string
  what: string
  built: boolean
  now?: (status: Status) => React.ReactNode
  run?: boolean
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
        {status.datasets.map((d) => (
          <li key={d.name}>
            <strong>{d.name}:</strong>{' '}
            {d.version
              ? `${number(d.version.n_reviews)} reviews, version ${shortId(d.version.revision)}, loaded ${when(d.version.loaded_at)}`
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
          Snapshot {status.case_metadata.snapshot_id}, collected {when(status.case_metadata.collected_at)}:{' '}
          {number(status.case_metadata.n_reviews)} library reviews, {number(status.case_metadata.n_details)} case
          details.
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
    now: (status) => (
      <ul className="facts-list">
        {status.datasets.map((d) => (
          <li key={d.name}>
            <strong>{d.name}:</strong>{' '}
            {d.sections
              ? `${number(d.sections.n_sections)} sections from ${number(d.sections.n_reviews)} reviews ` +
                `(${number(d.sections.n_across_pages)} cross a page break), from ${HEADINGS[d.sections.headings]}, ` +
                `grouped ${when(d.sections.grouped_at)}` +
                (d.sections.n_skipped ? `; ${number(d.sections.n_skipped)} reviews left out` : '')
              : d.version
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
    now: (status) => (
      <ul className="facts-list">
        {status.templates.map((t) => (
          <li key={t.guidance}>
            <strong>{GUIDANCES[t.guidance] ?? t.guidance}:</strong> {number(t.n_templates)} templates,{' '}
            {number(t.n_sections)} sections, read {when(t.loaded_at)}
          </li>
        ))}
        {status.datasets.map((d) => (
          <li key={d.name}>
            <strong>{d.name}:</strong>{' '}
            {d.candidates.length
              ? d.candidates
                  .map(
                    (c) =>
                      `${number(c.n_candidates)} candidates for ${number(c.n_sections)} sections against the ` +
                      `${GUIDANCES[c.guidance] ?? c.guidance} (${number(c.n_with_heading_match)} with a heading ` +
                      `match), found ${when(c.matched_at)}`,
                  )
                  .join('; ')
              : d.sections
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

export default function Overview({ status, goTo }: Props) {
  const representations = [
    { name: 'Page-based (as Datalab produced it)', built: true },
    { name: 'Hierarchical (blocks grouped by section)', built: true },
    { name: 'Standardised (sections mapped to the 2016 template)', built: false },
  ]
  return (
    <div className="page">
      <section className="intro">
        <h2>What O1 is</h2>
        <blockquote>
          <strong>O1. Derive a hierarchical, standardised representation of DHRC-100.</strong> Organise
          the DHRC-100 corpus by document hierarchy and standardise against the 2016 statutory guidance
          template (Appendix D). Store the result in a database linked to the Home Office DHR Library
          case metadata.
        </blockquote>
        <p>
          In other words: take the reviews as Datalab extracted them (page by page), rebuild each
          one’s structure (which paragraphs belong to which section), line every section up with the
          sections the 2016 guidance says an overview report should have, and keep all of it in one
          database next to what the Home Office’s library records about each case. Stage 2 (the LLM
          wiki) is built from this database; the case metadata is kept back to check the wiki’s
          answers later.
        </p>
      </section>

      <section>
        <h2>The O1 pipeline, step by step</h2>
        <p className="muted">
          These are the steps in the specification’s Stage 1 diagram (Appendix B). The first four are
          built and can be run from the <a onClick={() => goTo('run')}>Run</a> tab; the rest come next.
        </p>
        <ol className="pipeline">
          {STEPS.map((step, index) => (
            <li key={step.title} className={step.built ? 'step built' : 'step planned'}>
              <div className="step-number">{index + 1}</div>
              <div className="step-body">
                <div className="step-head">
                  <h3>{step.title}</h3>
                  <span className={step.built ? 'chip done' : 'chip planned'}>
                    {step.built ? 'Built' : 'Not built yet'}
                  </span>
                </div>
                <p>{step.what}</p>
                {step.built && status && step.now && <div className="step-now">{step.now(status)}</div>}
                {step.run && (
                  <button className="link-button" onClick={() => goTo('run')}>
                    Run it →
                  </button>
                )}
              </div>
            </li>
          ))}
          <li className="step result">
            <div className="step-number">⛁</div>
            <div className="step-body">
              <h3>Result: one queryable database, three representations</h3>
              <ul className="checklist">
                {representations.map((r) => (
                  <li key={r.name} className={r.built ? 'yes' : 'no'}>
                    {r.built ? '✓' : '○'} {r.name}
                  </li>
                ))}
                <li className="yes">✓ Each review linked to its case metadata</li>
              </ul>
            </div>
          </li>
        </ol>
      </section>

      <section>
        <h2>Using this app</h2>
        <dl className="guide">
          <dt>
            <a onClick={() => goTo('data')}>Data</a>
          </dt>
          <dd>The two datasets, what is loaded, where each review came from, and where to find it all in the database.</dd>
          <dt>
            <a onClick={() => goTo('run')}>Run</a>
          </dt>
          <dd>Each pipeline explained, with a button to run it and its progress as it runs. “Run all of O1” runs every built step in order.</dd>
          <dt>
            <a onClick={() => goTo('cases')}>Cases</a>
          </dt>
          <dd>Every review, with every case detail the DHR Library holds for it.</dd>
          <dt>
            <a onClick={() => goTo('attributes')}>Attributes</a>
          </dt>
          <dd>How often each case detail appears in each dataset compared with the whole library: how representative each dataset is.</dd>
        </dl>
      </section>

      <section>
        <h2>Changing the code (including this app)</h2>
        <p>
          The team code lives in GitHub (NarraTrace-4YP/4yp_platform), and you can change it in either place:
        </p>
        <ul>
          <li>
            <strong>In Windmill’s web editor.</strong> Your edit runs straight away. Within about 15 minutes GitHub
            opens a pull request called “Changes made in Windmill” with it, for someone to review and merge. Until
            it’s merged, deploys from GitHub wait rather than overwrite your edit.
          </li>
          <li>
            <strong>On GitHub,</strong> with a branch and a pull request. Merging it deploys it here within a couple
            of minutes.
          </li>
        </ul>
      </section>
    </div>
  )
}
