import React from 'react'
import { Build, GUIDANCES, STATUS_TEXT, formatDateTime, formatNumber, typeColour } from './shared'

type Props = { builds: Build[] | null; build: Build | null; onChoose: (id: string) => void }

export default function WikisTab({ builds, build, onChoose }: Props) {
  if (builds === null) return <div className="page muted">Loading…</div>
  if (!builds.length)
    return (
      <div className="page">
        <h2>No wikis yet</h2>
        <p>
          Run <code>f/dhr/O2/pipeline</code> in Windmill to build the first one. The Overview
          lists what to set up first.
        </p>
      </div>
    )
  return (
    <div className="page">
      <section>
        <h2>Every build</h2>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Wiki</th>
                <th>Status</th>
                <th>Reviews</th>
                <th className="num">Pages</th>
                <th className="num">Links</th>
                <th>Made</th>
              </tr>
            </thead>
            <tbody>
              {builds.map((b) => (
                <tr
                  key={b.instance_id}
                  className={b.instance_id === build?.instance_id ? 'selected' : ''}
                  onClick={() => onChoose(b.instance_id)}
                >
                  <td>
                    <button className="link-button" onClick={() => onChoose(b.instance_id)}>
                      {b.name}
                    </button>
                    <div className="muted small mono">{b.instance_id}</div>
                  </td>
                  <td>
                    <span className={`status status-${b.status}`}>{STATUS_TEXT[b.status]}</span>
                    {b.progress && (b.status === 'building' || b.status === 'uploading') && (
                      <div className="muted small">{b.progress.percent}%</div>
                    )}
                  </td>
                  <td>
                    {b.n_reviews} · {b.dataset}, {b.reviews}
                  </td>
                  <td className="num">{formatNumber(b.n_pages)}</td>
                  <td className="num">{formatNumber(b.n_links)}</td>
                  <td>
                    {formatDateTime(b.created_at)}
                    <div className="muted small">{b.created_by}</div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {build && <BuildDetail build={build} />}
    </div>
  )
}

// WeKnora's wiki builder reads this many characters of each file (f/dhr/O2/kb_settings.py).
const WIKI_READ_LIMIT = 32768

function BuildDetail({ build }: { build: Build }) {
  const types = Object.entries(build.pages_by_type).sort((a, b) => b[1] - a[1])
  const total = types.reduce((n, [, c]) => n + c, 0)
  const kb = build.settings.knowledge_base // undefined for builds from before 25 Nov 2026
  return (
    <section>
      <h2>{build.name}</h2>
      {build.status_note && <p className="note">{build.status_note}</p>}
      {build.progress && (build.status === 'building' || build.status === 'uploading') && (
        <div className="progress">
          <div
            className="progress-bar"
            role="progressbar"
            aria-valuenow={build.progress.percent}
            aria-valuemin={0}
            aria-valuemax={100}
          >
            <span style={{ width: `${build.progress.percent}%` }} />
          </div>
          <p className="small">
            {build.progress.line}
            <span className="muted"> · checked {formatDateTime(build.progress.at)}</span>
          </p>
        </div>
      )}
      <dl className="facts">
        <dt>Status</dt>
        <dd>
          <span className={`status status-${build.status}`}>{STATUS_TEXT[build.status]}</span>
          {build.finished_at && ` · finished ${formatDateTime(build.finished_at)}`}
        </dd>
        <dt>Reviews</dt>
        <dd>
          {build.n_reviews} from {build.dataset} ({build.reviews})
          {build.n_files > build.n_reviews && (
            <span className="muted"> · uploaded as {formatNumber(build.n_files)} files</span>
          )}
        </dd>
        <dt>Chunking</dt>
        <dd>
          {kb
            ? `${kb.chunk_strategy}, ${formatNumber(kb.chunk_size)} characters, ${formatNumber(kb.chunk_overlap)} overlap${kb.chunk_separators?.length ? `, separators ${kb.chunk_separators.map((s) => JSON.stringify(s)).join(' ')}` : ''}`
            : "WeKnora's defaults"}
        </dd>
        <dt>Knowledge graph</dt>
        <dd>{kb?.knowledge_graph ? 'on (see the Knowledge graph tab)' : 'off'}</dd>
        {kb && (kb.ingest_batch_size || kb.ingest_map_parallel || kb.ingest_reduce_parallel || kb.ingest_max_inflight) ? (
          <>
            <dt>Throughput</dt>
            <dd className="small">
              batch {kb.ingest_batch_size || 'default'} · read {kb.ingest_map_parallel || 'default'} ·
              write {kb.ingest_reduce_parallel || 'default'} · batches {kb.ingest_max_inflight || 'default'}
            </dd>
          </>
        ) : null}
        <dt>Template labels</dt>
        <dd>{GUIDANCES[build.settings.template_guidance ?? 'none'] ?? build.settings.template_guidance}</dd>
        <dt>Granularity</dt>
        <dd>{build.settings.granularity ?? 'standard'}</dd>
        <dt>Model</dt>
        <dd>
          <code>{build.model}</code>
        </dd>
        <dt>Last saved</dt>
        <dd>{formatDateTime(build.saved_at)}</dd>
        <dt>Code</dt>
        <dd className="mono small">{build.git_commit ?? '—'}</dd>
      </dl>

      {total > 0 && (
        <>
          <h3>Pages by type</h3>
          <div className="type-bar" role="img" aria-label="Pages by type">
            {types.map(([t, n]) => (
              <span key={t} style={{ flexGrow: n, background: typeColour(t) }} title={`${t}: ${n}`} />
            ))}
          </div>
          <ul className="legend">
            {types.map(([t, n]) => (
              <li key={t}>
                <span className="swatch" style={{ background: typeColour(t) }} /> {t}{' '}
                <span className="muted">{n}</span>
              </li>
            ))}
          </ul>
        </>
      )}

      <h3>Reviews in this wiki</h3>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Review</th>
              <th>Copy</th>
              <th>WeKnora</th>
              <th className="num">Characters</th>
              <th className="num">Joined</th>
              <th className="num">Pages citing it</th>
            </tr>
          </thead>
          <tbody>
            {build.documents.map((d) => (
              <tr key={d.doc_id}>
                <td>{d.doc_id}</td>
                <td>{d.dataset}</td>
                <td>
                  <span className={`status parse-${d.parse_status ?? 'none'}`}>
                    {d.parse_status ?? 'not uploaded'}
                  </span>
                  {d.error_message && <div className="error small">{d.error_message}</div>}
                </td>
                <td className="num">
                  {formatNumber(d.n_chars)}
                  {d.n_chars > WIKI_READ_LIMIT && <span className="muted"> *</span>}
                </td>
                <td className="num">{d.n_joined}</td>
                <td className="num">{d.n_pages}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted small">
        Joined: paragraphs rejoined across a page break before upload. * WeKnora’s wiki reads
        only the first {formatNumber(WIKI_READ_LIMIT)} characters of each review, so its pages
        come from the start of this one.
      </p>

      <h3>Deleting this wiki</h3>
      <p className="small">
        To delete it completely (WeKnora’s knowledge base, its pages and knowledge graph, the
        copy shown here, and the model job it left running on Blythe), run{' '}
        <code>f/dhr/O2/delete_wiki</code> in Windmill with <code>{build.instance_id}</code> as
        both <code>instance_id</code> and <code>confirm</code>. Only {build.created_by}, who
        made it, can.
      </p>

      <details className="instructions">
        <summary>The instructions this build gave WeKnora</summary>
        <h4>What to make pages for</h4>
        <pre>{build.settings.extraction_instructions ?? '—'}</pre>
        <h4>How to write them</h4>
        <pre>{build.settings.content_instructions ?? '—'}</pre>
        {kb?.graph_instructions && (
          <>
            <h4>What to put in the knowledge graph</h4>
            <pre>{kb.graph_instructions}</pre>
          </>
        )}
      </details>
    </section>
  )
}
