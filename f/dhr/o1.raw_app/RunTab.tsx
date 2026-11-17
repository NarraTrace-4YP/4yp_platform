import React, { useEffect, useRef, useState } from 'react'
import { backendAsync } from './wmill'
import { DATASETS, Status, minutes, number, when, windmillLink } from './shared'
import { Run, useRun } from './runs'

type Props = { status: Status | null; refresh: () => void }

const CODE = 'https://github.com/NarraTrace-4YP/4yp_platform/blob/main/f/dhr/'

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

// The O1 pipeline's steps (f/dhr/o1_pipeline.flow), by id.
const FLOW_STEPS: Record<string, string> = {
  load_dhrc_100: 'Load DHRC-100',
  load_dhrc_raw: 'Load DHRC-RAW',
  collect_case_metadata: 'Collect case metadata',
  group_sections_100: 'Group DHRC-100 by section',
  group_sections_raw: 'Group DHRC-RAW by section',
  status: 'Report where O1 stands',
}

function Elapsed({ since }: { since: string | null }) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [])
  if (!since) return null
  return <>{minutes(Math.max(0, now - Date.parse(since)))}</>
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
  const r = run.run
  if (!run.jobId && !run.error) return null
  const link = run.jobId ? windmillLink(status, `/run/${run.jobId}`) : null
  return (
    <div className={`run-panel ${r?.state ?? 'queued'}`}>
      <div className="run-head">
        <span className={`chip ${r?.state ?? 'queued'}`}>{r ? STATE_NAMES[r.state] : 'Starting…'}</span>
        {r?.started_at && r.state === 'running' && (
          <span className="muted">
            for <Elapsed since={r.started_at} />
          </span>
        )}
        {r?.duration_ms != null && r.state !== 'running' && <span className="muted">took {minutes(r.duration_ms)}</span>}
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

      {r?.steps && (
        <ol className="run-steps">
          {r.steps.map((s) => (
            <li key={s.id} className={s.state}>
              <span className={`dot ${s.state}`} /> {FLOW_STEPS[s.id] ?? s.id}
              <span className="muted"> · {STATE_NAMES[s.state] ?? s.state}</span>
            </li>
          ))}
        </ol>
      )}

      {r?.state === 'running' && (
        <div className="progress" aria-label="Progress">
          <div style={{ width: `${r.progress ?? 0}%` }} className={r.progress == null ? 'unknown' : ''} />
          <span>{r.progress == null ? 'Working…' : `${r.progress}%`}</span>
        </div>
      )}

      {r && <Log lines={r.log} />}

      {r?.state === 'failed' && (
        <p className="message error">
          <strong>It failed:</strong> {r.error ?? 'see the log above, or open it in Windmill for the whole log.'}
        </p>
      )}
      {r?.state === 'done' && <div className="run-result">{renderResult(r.result)}</div>}
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
          <code>f/dhr/{path}</code> ·{' '}
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
    <button className="primary" disabled={busy} onClick={() => (confirm ? setAsking(true) : onStart())}>
      {busy ? 'Running…' : label}
    </button>
  )
}

function LoadResult({ result }: { result: any }) {
  if (!result) return null
  return (
    <p>
      Stored {number(result.n_documents)} {result.dataset} reviews ({number(result.n_pages)} pages,{' '}
      {number(result.n_blocks)} blocks) as version <code>{String(result.revision).slice(0, 12)}</code>.
      {result.n_skipped > 0 && ` Skipped ${number(result.n_skipped)} that couldn’t be read (listed in the log).`}
    </p>
  )
}

function CollectResult({ result }: { result: any }) {
  if (!result) return null
  if (!result.snapshot_id) {
    return (
      <p>
        Quick check passed: the library listed {number(result.n_reviews)} reviews and {number(result.n_filters)}{' '}
        filters were searched. Nothing was stored.
      </p>
    )
  }
  return (
    <div>
      <p>
        Stored snapshot {result.snapshot_id}: {number(result.n_reviews)} library reviews, {number(result.n_filters)}{' '}
        filters, {number(result.n_details)} case details, in {result.minutes} minutes.
      </p>
      <ul>
        {Object.entries(result.datasets ?? {}).map(([name, c]: [string, any]) => (
          <li key={name}>
            {name}: {number(c.n_in_library)} of {number(c.n_documents)} in the library, {number(c.n_with_details)} with
            case details{c.n_elsewhere ? `, ${number(c.n_elsewhere)} published elsewhere` : ''}
          </li>
        ))}
      </ul>
    </div>
  )
}

function GroupResult({ result }: { result: any }) {
  if (!result) return null
  return (
    <div>
      <p>
        Grouped {number(result.n_documents)} {result.dataset} reviews into {number(result.n_sections)} sections (
        {number(result.n_across_pages)} cross a page break), {number(result.n_blocks)} blocks in all.
      </p>
      <p className="muted">
        {number(result.n_before_first_heading)} {result.n_before_first_heading === 1 ? 'block comes' : 'blocks come'}{' '}
        before the first heading (title pages, contents)
        {result.n_page_furniture > 0 && `; ${number(result.n_page_furniture)} page headers and footers left out`}
        {result.n_loose_headings > 0 &&
          `; ${number(result.n_loose_headings)} heading blocks sit outside the hierarchy and were kept as ordinary blocks`}
        .
      </p>
      {result.n_skipped > 0 && (
        <div>
          Left out {number(result.n_skipped)} review(s) whose headings don’t fit together:
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
  })
  const [revisions, setRevisions] = useState({ dhrc_100_revision: 'main', dhrc_raw_revision: 'main' })
  const nothing = !Object.values(steps).some(Boolean)
  return (
    <Card title="Run all of O1" path="o1_pipeline" kind="flow">
      <p>
        Builds O1 from scratch, one step after another, as a single run (a Windmill <em>flow</em>). It ends
        by reporting where O1 stands. As the remaining O1 steps are built they’ll be added here, so this
        stays the one button that rebuilds the whole of Stage 1.
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
          <p className="muted">“main” is the latest version; a commit id loads that exact version.</p>
        </details>
      </fieldset>
      <StartButton
        run={run}
        label={nothing ? 'Report where O1 stands' : 'Run all of O1'}
        confirm={
          steps.collect_case_metadata
            ? 'This includes the case metadata collection, so it takes about half an hour. Start?'
            : undefined
        }
        onStart={() =>
          run.start(() =>
            backendAsync.o1_pipeline({
              ...steps,
              dhrc_100_revision: revisions.dhrc_100_revision.trim() || 'main',
              dhrc_raw_revision: revisions.dhrc_raw_revision.trim() || 'main',
            }),
          )
        }
      />
      <RunPanel
        run={run}
        status={status}
        renderResult={() => <p>Finished. The Overview and Data tabs now show the new state of O1.</p>}
      />
    </Card>
  )
}

function LoadDataset({ status, refresh }: Props) {
  const run = useRun('load_dhrc', refresh)
  const [dataset, setDataset] = useState(DATASETS[0])
  const [revision, setRevision] = useState('main')
  return (
    <Card title="Load a dataset" path="load_dhrc" kind="script">
      <p>Puts one dataset’s reviews into the database, exactly as they are. In order, it:</p>
      <ol>
        <li>downloads the dataset from Hugging Face (the version you choose: “main” is the latest);</li>
        <li>checks every file against the dataset’s own checksums file, and stops if any file is missing or changed;</li>
        <li>
          checks each review reads as a document (pages containing blocks). In DHRC-100 one that doesn’t stops the
          load; in DHRC-RAW it’s skipped and listed in the log;
        </li>
        <li>
          stores every review untouched in <code>corpus.documents</code>, labelled with the dataset and version, all
          in one go (if anything fails, nothing is stored);
        </li>
        <li>records the run in MLflow (experiment “corpus-ingest”).</li>
      </ol>
      <p className="muted">
        Safe to re-run: loading the same version again replaces it. You don’t need to collect the case metadata
        again afterwards; it covers every dataset.
      </p>
      <div className="form-row">
        <label>
          Dataset{' '}
          <select value={dataset} onChange={(e) => setDataset(e.target.value)} disabled={run.active}>
            {DATASETS.map((d) => (
              <option key={d}>{d}</option>
            ))}
          </select>
        </label>
        <label>
          Version <input value={revision} onChange={(e) => setRevision(e.target.value)} disabled={run.active} />
        </label>
        <StartButton
          run={run}
          label={`Load ${dataset}`}
          onStart={() => run.start(() => backendAsync.load_dhrc({ dataset, revision: revision.trim() || 'main' }))}
        />
      </div>
      <RunPanel run={run} status={status} renderResult={(result) => <LoadResult result={result} />} />
    </Card>
  )
}

function CollectMetadata({ status, refresh }: Props) {
  const run = useRun('collect_case_metadata', refresh)
  const [full, setFull] = useState(false)
  const last = status?.case_metadata
  return (
    <Card title="Collect case metadata" path="collect_case_metadata" kind="script">
      <p>
        The Home Office DHR Library lists each review’s title, CSP, region and dates, but everything else it knows
        about a case (the victim’s age, the method of killing, the relationship, its themes…) only exists as search
        filters, with no download. So this asks the site one question per filter option (“which reviews have Sex
        of victim: Female?”), about 900 questions, one a second, and records which reviews come back.
      </p>
      <p>
        Before storing anything it checks the site is really filtering and that every page of results adds new
        reviews. The answers are stored as a new snapshot in the evaluation schema, which the wiki agent can’t
        read. It collects the whole library, so one snapshot covers every dataset.
      </p>
      {last && (
        <p className="muted">
          Last complete snapshot: {last.snapshot_id}, collected {when(last.collected_at)}.
        </p>
      )}
      <div className="form-row">
        <label>
          <input type="radio" checked={!full} onChange={() => setFull(false)} disabled={run.active} /> Quick check
          (10 filters, stores nothing, under a minute)
        </label>
        <label>
          <input type="radio" checked={full} onChange={() => setFull(true)} disabled={run.active} /> Full collection
          (about 20 minutes)
        </label>
        <StartButton
          run={run}
          label={full ? 'Collect' : 'Check the site'}
          confirm={full ? 'This asks the library about 900 questions over about 20 minutes. Start?' : undefined}
          onStart={() =>
            run.start(() =>
              backendAsync.collect_case_metadata(
                full
                  ? { max_filters: 0, delay_seconds: 1, dry_run: false }
                  : { max_filters: 10, delay_seconds: 1, dry_run: true },
              ),
            )
          }
        />
      </div>
      <RunPanel run={run} status={status} renderResult={(result) => <CollectResult result={result} />} />
    </Card>
  )
}

function GroupSections({ status, refresh }: Props) {
  const run = useRun('group_sections', refresh)
  const [dataset, setDataset] = useState(DATASETS[0])
  const current = status?.datasets.find((d) => d.name === dataset)
  return (
    <Card title="Group blocks by section" path="group_sections" kind="script">
      <p>
        Datalab gives each review page by page. This puts its blocks back in reading order under the headings they
        belong to, so a section that runs across a page break is one section again, and any section can be fetched
        with everything nested inside it. In order, it:
      </p>
      <ol>
        <li>takes the newest version loaded of the dataset you choose;</li>
        <li>
          works out each review’s structure. For DHRC-100 that comes from the headings the team checked by hand (each
          heading’s level and parent, and which heading every block belongs to). For DHRC-RAW it comes from
          Datalab’s own headings, which nobody has checked;
        </li>
        <li>
          checks the structure holds together: every block points to a heading that exists and comes before it,
          every heading sits under one at a higher level, and nothing from outside a section sits in the middle of
          it. In DHRC-100 one review that fails stops the run; in DHRC-RAW it’s left out and listed;
        </li>
        <li>leaves out page headers and footers (running titles, “Page 3 of 56”), which belong to no section;</li>
        <li>
          stores the sections in <code>corpus.sections</code> and the blocks in <code>corpus.blocks</code>, replacing
          any earlier grouping of that version, all in one go (if anything fails, nothing changes);
        </li>
        <li>records the run in MLflow (experiment “corpus-sections”).</li>
      </ol>
      <p className="muted">
        Run it again after loading a dataset: reloading a version removes its grouping. It takes about a minute for
        DHRC-RAW.
      </p>
      {current?.sections && (
        <p className="muted">
          {dataset} was last grouped {when(current.sections.grouped_at)}: {number(current.sections.n_sections)}{' '}
          sections from {number(current.sections.n_reviews)} reviews.
        </p>
      )}
      <div className="form-row">
        <label>
          Dataset{' '}
          <select value={dataset} onChange={(e) => setDataset(e.target.value)} disabled={run.active}>
            {DATASETS.map((d) => (
              <option key={d}>{d}</option>
            ))}
          </select>
        </label>
        <StartButton
          run={run}
          label={`Group ${dataset}`}
          onStart={() => run.start(() => backendAsync.group_sections({ dataset }))}
        />
      </div>
      <RunPanel run={run} status={status} renderResult={(result) => <GroupResult result={result} />} />
    </Card>
  )
}

export default function RunTab({ status, refresh }: Props) {
  return (
    <div className="page">
      <section>
        <h2>Run the pipelines</h2>
        <p className="muted">
          Each pipeline runs in Windmill, the same as clicking Run there, and keeps going if you close this page.
          Come back to see how it went. The code is the version on GitHub’s main branch, and every run records which
          commit it was.
        </p>
      </section>
      <RunAll status={status} refresh={refresh} />
      <LoadDataset status={status} refresh={refresh} />
      <CollectMetadata status={status} refresh={refresh} />
      <GroupSections status={status} refresh={refresh} />
    </div>
  )
}
