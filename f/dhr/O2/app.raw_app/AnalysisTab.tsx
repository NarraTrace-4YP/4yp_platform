// Fix (25 Nov 2026, problem review "O2 analysis runnable from the public app"):
//   Problem: the knowledge graph could be looked at but not measured: which agencies sit at
//   the centre of the reviews, which groups of entities belong together, and which reviews
//   look alike.
//   Why: nothing ran graph algorithms over it, and the O2 app can only show saved results.
//   Solution: f/dhr/O2/run_analysis runs centrality (degree, PageRank, betweenness),
//   communities (Louvain) and similarity (shared neighbours, shared organisations between
//   reviews) on the server's own Neo4j or networkx, and saves the results; this tab shows
//   them and has a Run button. Rowan allowed the button because the analysis never touches
//   Blythe. The runner guards it: one analysis at a time on the server, at most one a minute
//   per wiki, and only the last five runs per wiki are kept.
import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { backend } from './wmill'
import {
  AnalysisNode,
  AnalysisResponse,
  AnalysisRun,
  Build,
  errorMessage,
  formatDateTime,
  formatNumber,
} from './shared'

type Props = { build: Build | null }
type SortBy = 'pagerank' | 'betweenness' | 'degree'

const SORTS: { id: SortBy; label: string; hint: string }[] = [
  {
    id: 'pagerank',
    label: 'Influence',
    hint: 'PageRank: linked to by entities that are themselves well linked',
  },
  {
    id: 'betweenness',
    label: 'Bridging',
    hint: 'Betweenness: sits on the paths between other entities',
  },
  {
    id: 'degree',
    label: 'Connections',
    hint: 'Degree: how many entities it is linked to directly',
  },
]
const ENGINE_TEXT: Record<string, string> = {
  auto: 'Automatic (Neo4j if it has Graph Data Science, otherwise networkx)',
  gds: 'Neo4j Graph Data Science',
  networkx: 'networkx (Python, on the Windmill worker)',
}

function shortReview(docId: string): string {
  return docId.replace(/ - DHR$/, '').replace(/_a$/, '')
}
function score(n: number | null | undefined, digits = 4): string {
  if (n === null || n === undefined) return '—'
  return Math.abs(n) >= 100 ? Math.round(n).toLocaleString('en-GB') : n.toFixed(digits)
}
function duration(run: AnalysisRun): string {
  if (!run.started_at || !run.finished_at) return ''
  const s = Math.round((Date.parse(run.finished_at) - Date.parse(run.started_at)) / 1000)
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`
}

export default function AnalysisTab({ build }: Props) {
  const [data, setData] = useState<AnalysisResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [runId, setRunId] = useState(0)
  const [sortBy, setSortBy] = useState<SortBy>('pagerank')
  const [showTypes, setShowTypes] = useState(false)
  const [filter, setFilter] = useState('')
  const [engine, setEngine] = useState('auto')
  const [join, setJoin] = useState(true)
  const [running, setRunning] = useState(false)
  const [runMessage, setRunMessage] = useState<string | null>(null)
  const [chosen, setChosen] = useState<string | null>(null)

  const load = useCallback(
    (id: number) => {
      if (!build) return
      backend
        .analysis({ instance_id: build.instance_id, run_id: id, max_nodes: 500 })
        .then((r: AnalysisResponse) => {
          setData(r)
          setError(null)
        })
        .catch((e: unknown) => setError(errorMessage(e)))
    },
    [build?.instance_id],
  )

  useEffect(() => {
    setData(null)
    setRunId(0)
    setChosen(null)
    setRunMessage(null)
    load(0)
  }, [build?.instance_id, load])

  // While someone's analysis is running on the server, look again every 15 seconds.
  const busy = !!data?.busy_with
  useEffect(() => {
    if (!busy || running) return
    const timer = window.setInterval(() => load(runId), 15_000)
    return () => window.clearInterval(timer)
  }, [busy, running, runId, load])

  async function run() {
    if (!build) return
    setRunning(true)
    setRunMessage(null)
    try {
      const r: { run_id: number; engine: string } = await backend.run_analysis({
        instance_id: build.instance_id,
        engine,
        join_reviews: join,
      })
      setRunMessage(`Run ${r.run_id} finished (${r.engine}).`)
      setRunId(0)
      load(0)
    } catch (e) {
      setRunMessage(errorMessage(e))
      load(runId)
    } finally {
      setRunning(false)
    }
  }

  const result = data?.result ?? null
  const summary = result?.summary ?? null
  const nodeByKey = useMemo(() => new Map((result?.nodes ?? []).map((n) => [n.key, n])), [result])
  const rows = useMemo(() => {
    const q = filter.trim().toLowerCase()
    return [...(result?.nodes ?? [])]
      .filter((n) => showTypes || !n.is_type)
      .filter((n) => !q || n.name.toLowerCase().includes(q))
      .sort((a, b) => (b[sortBy] ?? 0) - (a[sortBy] ?? 0))
      .slice(0, 50)
  }, [result, sortBy, showTypes, filter])

  if (!build) return <div className="page muted">No wiki chosen yet.</div>
  if (!build.settings.knowledge_base?.knowledge_graph)
    return (
      <div className="page">
        <h2>No knowledge graph to analyse</h2>
        <p>This build was made without WeKnora’s knowledge graph.</p>
      </div>
    )

  const chosenNode = chosen ? nodeByKey.get(chosen) : undefined
  const runs = data?.runs ?? []
  const otherBusy = data?.busy_with && data.busy_with !== build.instance_id

  return (
    <div className="page">
      <section>
        <h2>Analysis</h2>
        <p>
          Graph measures over this wiki’s knowledge graph: which entities are most central, which
          groups belong together, and which reviews mention the same organisations. Named
          organisations (a national body such as the Crown Prosecution Service, or a named local one
          such as Wiltshire Police) are one entity across every review that names them; similar
          organisations (Wiltshire and Bromley Community Safety Partnerships) stay apart but share a
          type node (“Community Safety Partnership”). People, unnamed agencies (“the police”),
          events and dates stay with their review.
        </p>
        <div className="an-run">
          <label className="small">
            Engine{' '}
            <select value={engine} onChange={(e) => setEngine(e.target.value)} disabled={running}>
              {Object.entries(ENGINE_TEXT).map(([id, text]) => (
                <option key={id} value={id}>
                  {text}
                </option>
              ))}
            </select>
          </label>
          <label className="small">
            <input
              type="checkbox"
              checked={join}
              onChange={(e) => setJoin(e.target.checked)}
              disabled={running}
            />{' '}
            Join named organisations across reviews
          </label>
          <button
            className="button"
            onClick={run}
            disabled={running || !data?.has_graph || !!data?.busy_with}
          >
            {running ? 'Analysing… (up to a few minutes)' : 'Run analysis'}
          </button>
        </div>
        {data && !data.has_graph && (
          <p className="note">
            This wiki has no knowledge graph entities yet, so there is nothing to analyse.
          </p>
        )}
        {data?.busy_with && !running && (
          <p className="note">
            {otherBusy
              ? 'Another wiki is being analysed. One analysis runs at a time; try again in a minute.'
              : 'An analysis of this wiki is running. The results appear here when it finishes.'}
          </p>
        )}
        {runMessage && <p className="small">{runMessage}</p>}
        {error && <p className="error">Couldn’t load the analysis: {error}</p>}
      </section>

      {data && !runs.length && data.has_graph && (
        <section>
          <p className="muted">Not analysed yet. Press “Run analysis”.</p>
        </section>
      )}

      {result && summary && (
        <>
          <section>
            <h3>
              Run {result.run.run_id}{' '}
              <span className="muted small">
                {formatDateTime(result.run.finished_at)} · {result.run.engine ?? '—'}
                {result.run.settings.join_reviews === false && ' · reviews kept apart'}
                {duration(result.run) && ` · took ${duration(result.run)}`}
              </span>
            </h3>
            <div className="an-cards">
              {[
                ['Entities', summary.nodes],
                ['Relationships', summary.links],
                ['Reviews', summary.reviews],
                ['Shared by 2+ reviews', summary.shared_entities],
                ['Organisation types', summary.organisation_types],
                ['Communities', summary.communities],
                ['Separate pieces', summary.components],
                ['Largest piece', summary.largest_component],
              ]
                .filter(([, v]) => v !== undefined && v !== null)
                .map(([label, v]) => (
                  <div key={label as string} className="an-card">
                    <span className="an-value">{formatNumber(v as number)}</span>
                    <span className="small muted">{label}</span>
                  </div>
                ))}
            </div>
          </section>

          <section>
            <h3>Most central entities</h3>
            <div className="an-run">
              <div className="kg-switch small" role="radiogroup" aria-label="Rank by">
                Rank by{' '}
                {SORTS.map((s) => (
                  <button
                    key={s.id}
                    role="radio"
                    title={s.hint}
                    aria-checked={sortBy === s.id}
                    className={sortBy === s.id ? 'on' : ''}
                    onClick={() => setSortBy(s.id)}
                  >
                    {s.label}
                  </button>
                ))}
              </div>
              <label className="small">
                <input
                  type="checkbox"
                  checked={showTypes}
                  onChange={(e) => setShowTypes(e.target.checked)}
                />{' '}
                Include type nodes
              </label>
              <input
                type="search"
                placeholder="Filter by name…"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                aria-label="Filter entities"
              />
            </div>
            <p className="muted small">
              {SORTS.find((s) => s.id === sortBy)?.hint}. Click a name for its most similar
              entities.
            </p>
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Entity</th>
                    <th>Reviews</th>
                    <th className="num">Connections</th>
                    <th className="num">Influence</th>
                    <th className="num">Bridging</th>
                    <th className="num">Community</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((n) => (
                    <tr key={n.key} className={n.key === chosen ? 'selected' : ''}>
                      <td>
                        <button
                          className="link-button"
                          onClick={() => setChosen(n.key === chosen ? null : n.key)}
                        >
                          {n.name}
                        </button>
                        {n.is_type && <span className="an-tag">type</span>}
                      </td>
                      <td className="small">
                        {n.is_type || n.docs.length > 1
                          ? `${n.docs.length} reviews`
                          : shortReview(n.docs[0] ?? '')}
                      </td>
                      <td className="num">{formatNumber(n.degree)}</td>
                      <td className="num">{score(n.pagerank)}</td>
                      <td className="num">{score(n.betweenness)}</td>
                      <td className="num">{n.community ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {chosenNode && <SimilarCard node={chosenNode} onChoose={setChosen} known={nodeByKey} />}
          </section>

          {!!summary.top_communities?.length && (
            <section>
              <h3>Largest communities</h3>
              <p className="muted small">
                Groups of entities more linked to each other than to the rest (Louvain). A community
                spanning many reviews is held together by shared organisations or types.
              </p>
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th className="num">#</th>
                      <th className="num">Entities</th>
                      <th className="num">Reviews</th>
                      <th>Best connected</th>
                      <th>Organisation types</th>
                      <th>Main relationships</th>
                    </tr>
                  </thead>
                  <tbody>
                    {summary.top_communities.map((c) => (
                      <tr key={c.community}>
                        <td className="num">{c.community}</td>
                        <td className="num">{formatNumber(c.size)}</td>
                        <td className="num">{c.reviews}</td>
                        <td className="small">{[...new Set(c.top)].join(', ')}</td>
                        <td className="small">{c.types.join(', ') || '—'}</td>
                        <td className="small">{c.relationships.join(', ')}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}

          {!!summary.similar_reviews?.length && (
            <section>
              <h3>Reviews that look alike</h3>
              <p className="muted small">
                Pairs of reviews that name the same organisations (Jaccard similarity of their
                entities; organisations named in more than half the reviews are left out, as they
                would make every review look alike).
              </p>
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Review</th>
                      <th>Review</th>
                      <th className="num">Shared</th>
                      <th className="num">Similarity</th>
                      <th>For example</th>
                    </tr>
                  </thead>
                  <tbody>
                    {summary.similar_reviews.map((p) => (
                      <tr key={`${p.a}|${p.b}`}>
                        <td className="small">{shortReview(p.a)}</td>
                        <td className="small">{shortReview(p.b)}</td>
                        <td className="num">{p.shared}</td>
                        <td className="num">{score(p.score)}</td>
                        <td className="small">{p.examples.join(', ')}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}
        </>
      )}

      {runs.length > 0 && (
        <section>
          <h3>Runs of this wiki</h3>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th className="num">Run</th>
                  <th>Status</th>
                  <th>Engine</th>
                  <th>Started</th>
                  <th className="num">Entities</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {runs.map((r) => (
                  <tr key={r.run_id} className={r.run_id === result?.run.run_id ? 'selected' : ''}>
                    <td className="num">{r.run_id}</td>
                    <td>
                      <span
                        className={`status status-${r.status === 'done' ? 'built' : r.status === 'failed' ? 'failed' : 'building'}`}
                      >
                        {r.status}
                      </span>
                      {r.error && <div className="small error">{r.error}</div>}
                    </td>
                    <td className="small">
                      {r.engine ?? r.settings.engine ?? '—'}
                      {r.settings.join_reviews === false && ', reviews apart'}
                    </td>
                    <td className="small">{formatDateTime(r.started_at)}</td>
                    <td className="num">{formatNumber(r.nodes)}</td>
                    <td>
                      {r.status === 'done' && r.run_id !== result?.run.run_id && (
                        <button
                          className="link-button small"
                          onClick={() => {
                            setRunId(r.run_id)
                            setChosen(null)
                            load(r.run_id)
                          }}
                        >
                          Show
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  )
}

function SimilarCard({
  node,
  onChoose,
  known,
}: {
  node: AnalysisNode
  onChoose: (key: string) => void
  known: Map<string, AnalysisNode>
}) {
  return (
    <div className="an-similar">
      <p className="small muted">Entities linked to many of the same things as</p>
      <h4>{node.name}</h4>
      {node.similar.length ? (
        <ul className="small">
          {node.similar.map((s) => (
            <li key={s.key}>
              {known.has(s.key) ? (
                <button className="link-button" onClick={() => onChoose(s.key)}>
                  {s.name}
                </button>
              ) : (
                s.name
              )}{' '}
              <span className="muted">{score(s.score, 2)}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="small muted">None above the similarity cut-off (0.1).</p>
      )}
    </div>
  )
}
