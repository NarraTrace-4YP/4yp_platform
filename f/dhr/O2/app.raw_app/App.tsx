import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { backend } from './wmill'
import { Build, STATUS_TEXT, errorMessage, readStoredValue, writeStoredValue } from './shared'
import Overview from './Overview'
import WikisTab from './WikisTab'
import GraphTab from './GraphTab'
import KnowledgeGraphTab from './KnowledgeGraphTab'
import AnalysisTab from './AnalysisTab'
import PagesTab from './PagesTab'

const GROUPS = [
  {
    name: 'About O2',
    tabs: [{ id: 'overview', label: 'Overview', hint: 'How a wiki is built from O1' }],
  },
  {
    name: 'Results',
    tabs: [
      { id: 'wikis', label: 'Wikis', hint: 'Every build, its settings and reviews' },
      { id: 'graph', label: 'Graph', hint: 'How the chosen wiki’s pages link up' },
      {
        id: 'kgraph',
        label: 'Knowledge graph',
        hint: 'People, agencies and events, and how they relate (3D)',
      },
      {
        id: 'analysis',
        label: 'Analysis',
        hint: 'Central entities, communities and similar reviews',
      },
      { id: 'pages', label: 'Pages', hint: 'Read the chosen wiki page by page' },
    ],
  },
]
const TAB_IDS = GROUPS.flatMap((g) => g.tabs.map((t) => t.id))

export default function App() {
  const contentRef = useRef<HTMLElement>(null)
  const [tab, setTab] = useState(() => {
    const stored = readStoredValue<string>('tab')
    return stored && TAB_IDS.includes(stored) ? stored : 'overview'
  })
  const [builds, setBuilds] = useState<Build[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [buildId, setBuildId] = useState<string | null>(() => readStoredValue<string>('build'))
  const [pageSlug, setPageSlug] = useState<string | null>(null)

  useEffect(() => {
    document.title = 'O2: LLM wiki'
  }, [])

  const load = useCallback(() => {
    backend
      .wikis()
      .then((result: { builds: Build[] }) => {
        setBuilds(result.builds)
        setError(null)
      })
      .catch((e: unknown) => setError(errorMessage(e)))
  }, [])
  useEffect(load, [load])

  // While a build is going, refresh every minute so its status and pages keep up.
  const active = builds?.some((b) => b.status === 'uploading' || b.status === 'building')
  useEffect(() => {
    if (!active) return
    const timer = window.setInterval(load, 60_000)
    return () => window.clearInterval(timer)
  }, [active, load])

  const build = useMemo(() => {
    if (!builds?.length) return null
    return builds.find((b) => b.instance_id === buildId) ?? builds[0]
  }, [builds, buildId])

  const goTo = useCallback((id: string) => {
    setTab(id)
    writeStoredValue('tab', id)
    requestAnimationFrame(() => contentRef.current?.scrollTo({ top: 0 }))
  }, [])
  const chooseBuild = useCallback((id: string) => {
    setBuildId(id)
    setPageSlug(null)
    writeStoredValue('build', id)
  }, [])
  const openPage = useCallback(
    (slug: string) => {
      setPageSlug(slug)
      goTo('pages')
    },
    [goTo]
  )

  return (
    <div className="shell">
      <aside className="rail">
        <div className="rail-head">
          <p className="rail-stage">Stage 2 of 4</p>
          <h1>
            <span className="rail-o">O2</span> LLM wiki
          </h1>
        </div>
        <div className="rail-build">
          <label className="rail-group-name" htmlFor="build">
            Wiki shown
          </label>
          {builds === null && !error && <p className="muted small">Loading…</p>}
          {builds?.length === 0 && <p className="muted small">No wikis built yet.</p>}
          {!!builds?.length && build && (
            <>
              <select
                id="build"
                value={build.instance_id}
                onChange={(e) => chooseBuild(e.target.value)}
              >
                {builds.map((b) => (
                  <option key={b.instance_id} value={b.instance_id}>
                    {b.name}
                  </option>
                ))}
              </select>
              <p className="rail-build-meta">
                <span className={`status status-${build.status}`}>{STATUS_TEXT[build.status]}</span>{' '}
                {build.n_pages} pages · {build.n_reviews} reviews
              </p>
            </>
          )}
        </div>
        <nav className="rail-nav" aria-label="O2 views">
          {GROUPS.map((group) => (
            <div key={group.name} className="rail-group">
              <p className="rail-group-name">{group.name}</p>
              <ul>
                {group.tabs.map((t) => (
                  <li key={t.id}>
                    <button
                      className={t.id === tab ? 'tab selected' : 'tab'}
                      aria-current={t.id === tab ? 'page' : undefined}
                      onClick={() => goTo(t.id)}
                    >
                      <span className="tab-label">{t.label}</span>
                      <span className="tab-hint">{t.hint}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </nav>
        <button className="link-button rail-refresh" onClick={load}>
          Refresh
        </button>
      </aside>
      <main className="content" ref={contentRef}>
        {error && (
          <div className="page">
            <p className="error">Couldn’t load the wikis: {error}</p>
          </div>
        )}
        {tab === 'overview' && <Overview />}
        {tab === 'wikis' && (
          <WikisTab builds={builds} build={build} onChoose={chooseBuild} />
        )}
        {tab === 'graph' && <GraphTab build={build} onOpenPage={openPage} />}
        {tab === 'kgraph' && <KnowledgeGraphTab build={build} />}
        {tab === 'analysis' && <AnalysisTab build={build} />}
        {tab === 'pages' && (
          <PagesTab build={build} slug={pageSlug} onOpenPage={setPageSlug} />
        )}
      </main>
    </div>
  )
}
