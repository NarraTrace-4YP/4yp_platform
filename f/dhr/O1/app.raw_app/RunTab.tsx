import React, { useEffect, useRef, useState } from 'react'
import { backendAsync } from './wmill'
import {
  DATASETS,
  GUIDANCES,
  Status,
  formatDuration,
  formatNumber,
  formatDateTime,
  buildWindmillLink,
} from './shared'
import { Run, useRun } from './runs'

type Props = { status: Status | null; refresh: () => void }

const CODE = 'https://github.com/NarraTrace-4YP/4yp_platform/blob/main/f/dhr/O1/'

const STATE_NAMES: Record<string, string> = {
  queued: 'Waiting for a worker',
  running: 'Running',
  done: 'Finished',
  failed: 'Failed',
  cancelled: 'Cancelled',
  waiting: 'Waiting',
  starting: 'Starting',
  skipped: 'Skipped',
}

// The O1 pipeline's steps (f/dhr/O1/pipeline.flow), by id.
const FLOW_STEPS: Record<string, string> = {
  load_dhrc_100: 'Load DHRC-100',
  load_dhrc_raw: 'Load DHRC-RAW',
  collect_case_metadata: 'Collect case metadata',
  group_sections_100: 'Group DHRC-100 by section',
  group_sections_raw: 'Group DHRC-RAW by section',
  template_candidates_100: 'Find DHRC-100’s template candidates',
  template_candidates_raw: 'Find DHRC-RAW’s template candidates',
  status: 'Report where O1 stands',
}

function Elapsed({ since }: { since: string | null }) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [])
  if (!since) return null
  return <>{formatDuration(Math.max(0, now - Date.parse(since)))}</>
}

function Log({ lines }: { lines: string[] }) {
  const box = useRef<HTMLPreElement>(null)
  useEffect(() => {
    if (box.current) box.current.scrollTop = box.current.scrollHeight
  }, [lines])
  if (!lines.length) return null
  return (
    <pre className="log" ref={box}>
      {lines.join('\n')}
    </pre>
  )
}

// What every card shows about its run: state, progress, steps, log, result.
function RunPanel({
  run,
  status,
  renderResult,
}: {
  run: Run
  status: Status | null
  renderResult: (result: any) => React.ReactNode
}) {
  const runStatus = run.run
  if (!run.jobId && !run.error) return null
  const link = run.jobId ? buildWindmillLink(status, `/run/${run.jobId}`) : null
  return (
    <div className={`run-panel ${runStatus?.state ?? 'queued'}`}>
      <div className="run-head">
        <span className={`chip ${runStatus?.state ?? 'queued'}`}>
          {runStatus ? STATE_NAMES[runStatus.state] : 'Starting…'}
        </span>
        {runStatus?.started_at && runStatus.state === 'running' && (
          <span className="muted">
            for <Elapsed since={runStatus.started_at} />
          </span>
        )}
        {runStatus?.duration_ms != null && runStatus.state !== 'running' && (
          <span className="muted">took {formatDuration(runStatus.duration_ms)}</span>
        )}
        <span className="spacer" />
        {link && (
          <a href={link} target="_blank" rel="noreferrer">
            Open in Windmill ↗
          </a>
        )}
        {!run.active && (
          <button className="link-button" onClick={run.clear}>
            Dismiss
          </button>
        )}
      </div>

      {run.error && (
        <p className="message error">
          Couldn’t check the run: {run.error}{' '}
          <button className="link-button" onClick={run.checkAgain}>
            Check again
          </button>
        </p>
      )}

      {runStatus?.steps && (
        <ol className="run-steps">
          {runStatus.steps.map((step) => (
            <li key={step.id} className={step.state}>
              <span className={`dot ${step.state}`} /> {FLOW_STEPS[step.id] ?? step.id}
              <span className="muted"> · {STATE_NAMES[step.state] ?? step.state}</span>
            </li>
          ))}
        </ol>
      )}

      {runStatus?.state === 'running' && (
        <div className="progress" aria-label="Progress">
          <div
            style={{ width: `${runStatus.progress ?? 0}%` }}
            className={runStatus.progress == null ? 'unknown' : ''}
          />
          <span>{runStatus.progress == null ? 'Working…' : `${runStatus.progress}%`}</span>
        </div>
      )}

      {runStatus && <Log lines={runStatus.log} />}

      {runStatus?.state === 'failed' && (
        <p className="message error">
          <strong>It failed:</strong>{' '}
          {runStatus.error ?? 'see the log above, or open it in Windmill for the whole log.'}
        </p>
      )}
      {runStatus?.state === 'done' && (
        <div className="run-result">{renderResult(runStatus.result)}</div>
      )}
    </div>
  )
}

function Card({
  title,
  path,
  kind,
  children,
}: {
  title: string
  path: string
  kind: 'script' | 'flow'
  children: React.ReactNode
}) {
  const code = kind === 'flow' ? `${CODE}${path}.flow/flow.yaml` : `${CODE}${path}.py`
  return (
    <article className="card run-card">
      <header className="card-head">
        <h3>{title}</h3>
        <span className="muted">
          <code>f/dhr/O1/{path}</code> ·{' '}
          <a href={code} target="_blank" rel="noreferrer">
            code ↗
          </a>
        </span>
      </header>
      {children}
    </article>
  )
}

// A start button that asks "are you sure?" first for long runs.
function StartButton({
  run,
  label,
  confirm,
  onStart,
}: {
  run: Run
  label: string
  confirm?: string
  onStart: () => void
}) {
  const [asking, setAsking] = useState(false)
  const busy = run.active || run.starting
  if (asking) {
    return (
      <div className="confirm">
        <span>{confirm}</span>
        <button
          className="primary"
          onClick={() => {
            setAsking(false)
            onStart()
          }}
        >
          Yes, start
        </button>
        <button onClick={() => setAsking(false)}>Not now</button>
      </div>
    )
  }
  return (
    <button
      className="primary"
      disabled={busy}
      onClick={() => (confirm ? setAsking(true) : onStart())}
    >
      {busy ? 'Running…' : label}
    </button>
  )
}

function LoadResult({ result }: { result: any }) {
  if (!result) return null
  return (
    <p>
      Stored {formatNumber(result.n_documents)} {result.dataset} reviews (
      {formatNumber(result.n_pages)} pages, {formatNumber(result.n_blocks)} blocks) as version{' '}
      <code>{String(result.revision).slice(0, 12)}</code>.
      {result.n_skipped > 0 &&
        ` Skipped ${formatNumber(result.n_skipped)} that couldn’t be read (listed in the log).`}
    </p>
  )
}

function CollectResult({ result }: { result: any }) {
  if (!result) return null
  if (!result.snapshot_id) {
    return (
      <p>
        Quick check passed: the library listed {formatNumber(result.n_reviews)} reviews and{' '}
        {formatNumber(result.n_filters)} filters were searched. Nothing was stored.
      </p>
    )
  }
  return (
    <div>
      <p>
        Stored snapshot {result.snapshot_id}: {formatNumber(result.n_reviews)} library reviews,{' '}
        {formatNumber(result.n_filters)} filters, {formatNumber(result.n_details)} case details, in{' '}
        {result.minutes} minutes.
      </p>
      <ul>
        {Object.entries(result.datasets ?? {}).map(([name, c]: [string, any]) => (
          <li key={name}>
            {name}: {formatNumber(c.n_in_library)} of {formatNumber(c.n_documents)} in the library,{' '}
            {formatNumber(c.n_with_details)} with case details
            {c.n_elsewhere ? `, ${formatNumber(c.n_elsewhere)} published elsewhere` : ''}
          </li>
        ))}
      </ul>
    </div>
  )
}

function GroupResult({ result }: { result: any }) {
  if (!result) return null
  if (result.stored === false) {
    return (
      <div>
        <p>
          Checked {formatNumber(result.n_documents + result.n_skipped)} {result.dataset} reviews:{' '}
          {formatNumber(result.n_documents)} fit together ({formatNumber(result.n_sections)}{' '}
          sections) and {formatNumber(result.n_skipped)} don’t. Nothing was stored.
        </p>
        {result.n_skipped > 0 && (
          <ul>
            {(result.skipped ?? []).map((s: string) => (
              <li key={s}>{s}</li>
            ))}
          </ul>
        )}
      </div>
    )
  }
  return (
    <div>
      <p>
        Grouped {formatNumber(result.n_documents)} {result.dataset} reviews into{' '}
        {formatNumber(result.n_sections)} sections ({formatNumber(result.n_across_pages)} cross a
        page break), {formatNumber(result.n_blocks)} blocks in all.
      </p>
      <p className="muted">
        {formatNumber(result.n_before_first_heading)}{' '}
        {result.n_before_first_heading === 1 ? 'block comes' : 'blocks come'} before the first
        heading (title pages, contents)
        {result.n_page_furniture > 0 &&
          `; ${formatNumber(result.n_page_furniture)} page headers and footers left out`}
        {result.n_loose_headings > 0 &&
          `; ${formatNumber(result.n_loose_headings)} heading blocks sit outside the hierarchy and were kept as ordinary blocks`}
        {result.n_moved_headings > 0 &&
          `; ${formatNumber(result.n_moved_headings)} headings printed below the start of their own section were moved up to it`}
        .
      </p>
      {result.n_skipped > 0 && (
        <div>
          Left out {formatNumber(result.n_skipped)} review(s) whose headings don’t fit together:
          <ul>
            {(result.skipped ?? []).slice(0, 20).map((s: string) => (
              <li key={s}>{s}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

function RunAll({ status, refresh }: Props) {
  const run = useRun('o1_pipeline', refresh)
  const [steps, setSteps] = useState({
    load_dhrc_100: true,
    load_dhrc_raw: true,
    collect_case_metadata: true,
    group_sections_100: true,
    group_sections_raw: true,
    template_candidates_100: true,
    template_candidates_raw: true,
  })
  const [revisions, setRevisions] = useState({
    dhrc_100_revision: 'main',
    dhrc_raw_revision: 'main',
  })
  const [guidance, setGuidance] = useState('dhr-2016')
  const nothing = !Object.values(steps).some(Boolean)
  return (
    <Card title="Run all of O1" path="pipeline" kind="flow">
      <p>
        Builds O1 from scratch, one step after another, as a single run (a Windmill <em>flow</em>).
        It ends by reporting where O1 stands. As the remaining O1 steps are built they’ll be added
        here, so this stays the one button that rebuilds the whole of Stage 1.
      </p>
      <fieldset disabled={run.active}>
        <legend>Steps to run</legend>
        <label>
          <input
            type="checkbox"
            checked={steps.load_dhrc_100}
            onChange={(e) => setSteps({ ...steps, load_dhrc_100: e.target.checked })}
          />{' '}
          Load DHRC-100
        </label>
        <label>
          <input
            type="checkbox"
            checked={steps.load_dhrc_raw}
            onChange={(e) => setSteps({ ...steps, load_dhrc_raw: e.target.checked })}
          />{' '}
          Load DHRC-RAW
        </label>
        <label>
          <input
            type="checkbox"
            checked={steps.collect_case_metadata}
            onChange={(e) => setSteps({ ...steps, collect_case_metadata: e.target.checked })}
          />{' '}
          Collect case metadata (about 20 minutes)
        </label>
        <label>
          <input
            type="checkbox"
            checked={steps.group_sections_100}
            onChange={(e) => setSteps({ ...steps, group_sections_100: e.target.checked })}
          />{' '}
          Group DHRC-100 by section
        </label>
        <label>
          <input
            type="checkbox"
            checked={steps.group_sections_raw}
            onChange={(e) => setSteps({ ...steps, group_sections_raw: e.target.checked })}
          />{' '}
          Group DHRC-RAW by section
        </label>
        <label>
          <input
            type="checkbox"
            checked={steps.template_candidates_100}
            onChange={(e) => setSteps({ ...steps, template_candidates_100: e.target.checked })}
          />{' '}
          Find DHRC-100’s template candidates (about 5 minutes)
        </label>
        <label>
          <input
            type="checkbox"
            checked={steps.template_candidates_raw}
            onChange={(e) => setSteps({ ...steps, template_candidates_raw: e.target.checked })}
          />{' '}
          Find DHRC-RAW’s template candidates (about 40 minutes)
        </label>
        <label>
          Templates from the{' '}
          <select value={guidance} onChange={(e) => setGuidance(e.target.value)}>
            {Object.entries(GUIDANCES).map(([key, name]) => (
              <option key={key} value={key}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <details>
          <summary>Dataset versions</summary>
          <label>
            DHRC-100{' '}
            <input
              value={revisions.dhrc_100_revision}
              onChange={(e) => setRevisions({ ...revisions, dhrc_100_revision: e.target.value })}
            />
          </label>
          <label>
            DHRC-RAW{' '}
            <input
              value={revisions.dhrc_raw_revision}
              onChange={(e) => setRevisions({ ...revisions, dhrc_raw_revision: e.target.value })}
            />
          </label>
          <p className="muted">
            “main” is the latest version; a commit id loads that exact version.
          </p>
        </details>
      </fieldset>
      <StartButton
        run={run}
        label={nothing ? 'Report where O1 stands' : 'Run all of O1'}
        confirm={
          steps.template_candidates_raw
            ? 'This includes finding DHRC-RAW’s template candidates, so it takes about 40 minutes. Start?'
            : steps.collect_case_metadata
              ? 'This includes the case metadata collection, so it takes about half an hour. Start?'
              : undefined
        }
        onStart={() =>
          run.start(() =>
            backendAsync.pipeline({
              ...steps,
              dhrc_100_revision: revisions.dhrc_100_revision.trim() || 'main',
              dhrc_raw_revision: revisions.dhrc_raw_revision.trim() || 'main',
              template_guidance: guidance,
            }),
          )
        }
      />
      <RunPanel
        run={run}
        status={status}
        renderResult={() => (
          <p>Finished. The Overview and Data tabs now show the new state of O1.</p>
        )}
      />
    </Card>
  )
}

function LoadReviews({ status, refresh }: Props) {
  const run = useRun('load_dhrc', refresh)
  const [dataset, setDataset] = useState(DATASETS[0])
  const [revision, setRevision] = useState('main')
  return (
    <Card title="Load reviews" path="load_reviews/load_reviews" kind="script">
      <p>Puts one dataset’s reviews into the database, exactly as they are. In order, it:</p>
      <ol>
        <li>
          downloads the dataset from Hugging Face (the version you choose: “main” is the latest);
        </li>
        <li>
          checks every file against the dataset’s own checksums file, and stops if any file is
          missing or changed;
        </li>
        <li>
          checks each review reads as a document (pages containing blocks). In DHRC-100 one that
          doesn’t stops the load; in DHRC-RAW it’s skipped and listed in the log;
        </li>
        <li>
          stores every review untouched in <code>corpus.documents</code>, labelled with the dataset
          and version, all in one go (if anything fails, nothing is stored);
        </li>
        <li>records the run in MLflow (experiment “corpus-ingest”).</li>
      </ol>
      <p className="muted">
        Safe to re-run: loading the same version again replaces it. You don’t need to collect the
        case metadata again afterwards; it covers every dataset.
      </p>
      <div className="form-row">
        <label>
          Dataset{' '}
          <select
            value={dataset}
            onChange={(e) => setDataset(e.target.value)}
            disabled={run.active}
          >
            {DATASETS.map((d) => (
              <option key={d}>{d}</option>
            ))}
          </select>
        </label>
        <label>
          Version{' '}
          <input
            value={revision}
            onChange={(e) => setRevision(e.target.value)}
            disabled={run.active}
          />
        </label>
        <StartButton
          run={run}
          label={`Load ${dataset}`}
          onStart={() =>
            run.start(() =>
              backendAsync.load_reviews({ dataset, revision: revision.trim() || 'main' }),
            )
          }
        />
      </div>
      <RunPanel
        run={run}
        status={status}
        renderResult={(result) => <LoadResult result={result} />}
      />
    </Card>
  )
}

function LinkCaseMetadata({ status, refresh }: Props) {
  const run = useRun('collect_case_metadata', refresh)
  const [full, setFull] = useState(false)
  const last = status?.case_metadata
  return (
    <Card title="Link case metadata" path="link_case_metadata/link_case_metadata" kind="script">
      <p>
        The Home Office DHR Library lists each review’s title, CSP, region and dates, but everything
        else it knows about a case (the victim’s age, the method of killing, the relationship, its
        themes…) only exists as search filters, with no download. So this asks the site one question
        per filter option (“which reviews have Sex of victim: Female?”), about 900 questions, one a
        second, and records which reviews come back.
      </p>
      <p>
        Before storing anything it checks the site is really filtering and that every page of
        results adds new reviews. The answers are stored as a new snapshot in the evaluation schema,
        which the wiki agent can’t read. It collects the whole library, so one snapshot covers every
        dataset.
      </p>
      {last && (
        <p className="muted">
          Last complete snapshot: {last.snapshot_id}, collected {formatDateTime(last.collected_at)}.
        </p>
      )}
      <div className="form-row">
        <label>
          <input
            type="radio"
            checked={!full}
            onChange={() => setFull(false)}
            disabled={run.active}
          />{' '}
          Quick check (10 filters, stores nothing, under a minute)
        </label>
        <label>
          <input type="radio" checked={full} onChange={() => setFull(true)} disabled={run.active} />{' '}
          Full collection (about 20 minutes)
        </label>
        <StartButton
          run={run}
          label={full ? 'Collect' : 'Check the site'}
          confirm={
            full
              ? 'This asks the library about 900 questions over about 20 minutes. Start?'
              : undefined
          }
          onStart={() =>
            run.start(() =>
              backendAsync.link_case_metadata(
                full
                  ? { max_filters: 0, delay_seconds: 1, dry_run: false }
                  : { max_filters: 10, delay_seconds: 1, dry_run: true },
              ),
            )
          }
        />
      </div>
      <RunPanel
        run={run}
        status={status}
        renderResult={(result) => <CollectResult result={result} />}
      />
    </Card>
  )
}

function GroupBlocksBySection({ status, refresh }: Props) {
  const run = useRun('group_sections', refresh)
  const [dataset, setDataset] = useState(DATASETS[0])
  const [checkOnly, setCheckOnly] = useState(false)
  const current = status?.datasets.find((d) => d.name === dataset)
  return (
    <Card
      title="Group blocks by section"
      path="group_blocks_by_section/group_blocks_by_section"
      kind="script"
    >
      <p>
        Datalab gives each review page by page. This puts its blocks back in reading order under the
        headings they belong to, so a section that runs across a page break is one section again,
        and any section can be fetched with everything nested inside it. In order, it:
      </p>
      <ol>
        <li>takes the newest version loaded of the dataset you choose;</li>
        <li>
          works out each review’s structure. For DHRC-100 that comes from the headings the team
          checked by hand (each heading’s level and parent, and which heading every block belongs
          to). For DHRC-RAW it comes from Datalab’s own headings, which nobody has checked;
        </li>
        <li>
          puts a heading back at the start of its own section where the page prints it lower down
          (an appendix’s title above its “APPENDIX 1” label). It only ever moves past blocks that
          belong inside that section;
        </li>
        <li>
          checks the structure holds together: every block points to a heading that exists and comes
          before it, every heading sits under one at a higher level, and nothing from outside a
          section sits in the middle of it. In DHRC-100, if any review fails nothing is stored and
          every failing review is listed; in DHRC-RAW a failing review is left out and listed;
        </li>
        <li>
          leaves out page headers and footers (running titles, “Page 3 of 56”), which belong to no
          section;
        </li>
        <li>
          stores the sections in <code>corpus.sections</code> and the blocks in{' '}
          <code>corpus.blocks</code>, replacing any earlier grouping of that version, all in one go
          (if anything fails, nothing changes);
        </li>
        <li>records the run in MLflow (experiment “corpus-sections”).</li>
      </ol>
      <p className="muted">
        Run it again after loading a dataset: reloading a version removes its grouping. It takes
        about a minute for DHRC-RAW.
      </p>
      {current?.sections && (
        <p className="muted">
          {dataset} was last grouped {formatDateTime(current.sections.grouped_at)}:{' '}
          {formatNumber(current.sections.n_sections)} sections from{' '}
          {formatNumber(current.sections.n_reviews)} reviews.
        </p>
      )}
      <div className="form-row">
        <label>
          Dataset{' '}
          <select
            value={dataset}
            onChange={(e) => setDataset(e.target.value)}
            disabled={run.active}
          >
            {DATASETS.map((d) => (
              <option key={d}>{d}</option>
            ))}
          </select>
        </label>
        <label>
          <input
            type="checkbox"
            checked={checkOnly}
            onChange={(e) => setCheckOnly(e.target.checked)}
            disabled={run.active}
          />{' '}
          Check only (list every review that doesn’t fit; store nothing)
        </label>
        <StartButton
          run={run}
          label={checkOnly ? `Check ${dataset}` : `Group ${dataset}`}
          onStart={() =>
            run.start(() =>
              backendAsync.group_blocks_by_section({ dataset, check_only: checkOnly }),
            )
          }
        />
      </div>
      <RunPanel
        run={run}
        status={status}
        renderResult={(result) => <GroupResult result={result} />}
      />
    </Card>
  )
}

function CandidatesResult({ result }: { result: any }) {
  if (!result) return null
  const perSection = (result.n_candidates / Math.max(1, result.n_sections)).toFixed(1)
  return (
    <div>
      <p>
        Found {formatNumber(result.n_candidates)} candidates for {formatNumber(result.n_sections)}{' '}
        sections of {formatNumber(result.n_reviews)} {result.dataset} reviews ({perSection} per
        section), against the {GUIDANCES[result.guidance] ?? result.guidance}, in{' '}
        {formatDuration(result.seconds * 1000)}.
      </p>
      <p className="muted">
        {formatNumber(result.n_sections_with_heading_candidate)} sections have a template heading at
        least {result.min_heading_score}/100 alike their own.{' '}
        {formatNumber(result.n_named_sections)} are headed exactly as a template section; for{' '}
        {formatNumber(result.n_named_text_top1)} of those, that section is also their best match by
        text, and for {formatNumber(result.n_named_text_top3)} one of their best three.
      </p>
    </div>
  )
}

function FindCandidates({ status, refresh }: Props) {
  const run = useRun('find_template_candidates', refresh)
  const [dataset, setDataset] = useState(DATASETS[0])
  const [guidance, setGuidance] = useState('dhr-2016')
  const current = status?.datasets
    .find((d) => d.name === dataset)
    ?.candidates.find((c) => c.guidance === guidance)
  return (
    <Card
      title="Find candidate template sections"
      path="find_candidate_template_sections/find_candidate_template_sections"
      kind="script"
    >
      <p>
        The 2016 statutory guidance sets out templates for what a review produces: the overview
        report (Appendix Three), its executive summary (Four), the agencies’ own reports (individual
        management reviews, One and Two) and an example action plan (Five). Reviews don’t all follow
        them and word their headings differently, so this gives every section a short list of
        template sections it might be. The next step has an LLM choose among them. You can also
        match against the September 2026 guidance (which renamed the reviews DARDRs): its DARDR
        template, action plan, scoping review and IMR (Annexes A to D). A dataset keeps its
        candidates against both. In order, it:
      </p>
      <ol>
        <li>
          downloads the guidance you choose from GOV.UK (for 2026: the guidance PDF, and the DARDR
          template’s own Word file) and reads its templates (each heading, and what the guidance
          says goes there), stopping if a file no longer has the headings expected. They’re stored
          with each file’s checksum, once, and again only if a file changes;
        </li>
        <li>takes every section of the newest grouped version of the dataset you choose;</li>
        <li>
          compares each section’s heading with every template heading, as words (0 to 100), after
          dropping numbering (“3.2”, “Two:”), small words and plural endings, so “Lessons Learned”
          matches “Lessons to be learnt”;
        </li>
        <li>
          compares each section’s text (its heading, the start of its own text and its subsections’
          headings) with what the guidance says goes in each template section, by meaning, using a
          small open embedding model (BAAI’s bge-small) on the server’s CPU;
        </li>
        <li>
          keeps as candidates the 5 best each way (by heading only if at least 60/100 alike), with
          both scores, in <code>corpus.template_candidates</code>, replacing any earlier candidates
          for that version against the same guidance;
        </li>
        <li>records the run in MLflow (experiment “template-candidates”).</li>
      </ol>
      <p className="muted">
        Run it again after grouping a dataset by section: regrouping removes the old candidates. It
        takes about 5 minutes for DHRC-100 and about 40 for DHRC-RAW (the model reads every
        section).
      </p>
      {current && (
        <p className="muted">
          {dataset}’s candidates against the {GUIDANCES[guidance]} were last found{' '}
          {formatDateTime(current.matched_at)}: {formatNumber(current.n_candidates)} for{' '}
          {formatNumber(current.n_sections)} sections.
        </p>
      )}
      <div className="form-row">
        <label>
          Dataset{' '}
          <select
            value={dataset}
            onChange={(e) => setDataset(e.target.value)}
            disabled={run.active}
          >
            {DATASETS.map((d) => (
              <option key={d}>{d}</option>
            ))}
          </select>
        </label>
        <label>
          Templates from the{' '}
          <select
            value={guidance}
            onChange={(e) => setGuidance(e.target.value)}
            disabled={run.active}
          >
            {Object.entries(GUIDANCES).map(([key, name]) => (
              <option key={key} value={key}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <StartButton
          run={run}
          label={`Find ${dataset}’s candidates`}
          onStart={() =>
            run.start(() => backendAsync.find_candidate_template_sections({ dataset, guidance }))
          }
        />
      </div>
      <RunPanel
        run={run}
        status={status}
        renderResult={(result) => <CandidatesResult result={result} />}
      />
    </Card>
  )
}

export default function RunTab({ status, refresh }: Props) {
  return (
    <div className="page">
      <section>
        <h2>Run the pipelines</h2>
        <p className="muted">
          Each pipeline runs in Windmill, the same as clicking Run there, and keeps going if you
          close this page. Come back to see how it went. The code is the version on GitHub’s main
          branch, and every run records which commit it was.
        </p>
      </section>
      <RunAll status={status} refresh={refresh} />
      <LoadReviews status={status} refresh={refresh} />
      <LinkCaseMetadata status={status} refresh={refresh} />
      <GroupBlocksBySection status={status} refresh={refresh} />
      <FindCandidates status={status} refresh={refresh} />
    </div>
  )
}
