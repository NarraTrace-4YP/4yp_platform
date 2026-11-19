import React, { useCallback, useEffect, useRef, useState } from 'react'
import { backend } from './wmill'
import { Status, errorMessage, readStoredValue, writeStoredValue } from './shared'
import Overview, { STEP_BUILT } from './Overview'
import DataTab from './DataTab'
import RunTab from './RunTab'
import CasesTab from './CasesTab'
import AttributesTab from './AttributesTab'
import SectionsTab from './SectionsTab'
import CandidatesTab from './CandidatesTab'

// The views, grouped by what you come to do. Each says in a line what it shows.
const GROUPS = [
  {
    name: 'About O1',
    tabs: [
      { id: 'overview', label: 'Overview', hint: 'What O1 is, step by step' },
      { id: 'data', label: 'Data', hint: 'The datasets and where reviews came from' },
    ],
  },
  {
    name: 'Pipelines',
    tabs: [{ id: 'run', label: 'Run', hint: 'Start a step and follow it' }],
  },
  {
    name: 'Results',
    tabs: [
      { id: 'cases', label: 'Cases', hint: 'Every review and its case details' },
      { id: 'attributes', label: 'Attributes', hint: 'How representative each dataset is' },
      { id: 'sections', label: 'Sections', hint: 'Each review’s outline and text' },
      {
        id: 'candidates',
        label: 'Template candidates',
        hint: 'The template sections each section might be',
      },
    ],
  },
]
const TAB_IDS = GROUPS.flatMap((group) => group.tabs.map((t) => t.id))

export default function App() {
  const contentRef = useRef<HTMLElement>(null)
  const [tab, setTab] = useState(() => {
    const stored = readStoredValue<string>('tab')
    return stored && TAB_IDS.includes(stored) ? stored : 'overview'
  })
  const [status, setStatus] = useState<Status | null>(null)
  const [statusError, setStatusError] = useState<string | null>(null)
  // Goes up each time a run finishes, so every tab reloads what it shows.
  const [version, setVersion] = useState(0)

  const goTo = useCallback((id: string) => {
    setTab(id)
    writeStoredValue('tab', id)
    requestAnimationFrame(() => contentRef.current?.scrollTo({ top: 0, behavior: 'auto' }))
  }, [])

  const refresh = useCallback(() => setVersion((v) => v + 1), [])

  useEffect(() => {
    document.title = 'O1: Corpus preparation'
  }, [])

  useEffect(() => {
    let current = true
    backend
      .status()
      .then((result: Status) => {
        if (!current) return
        setStatus(result)
        setStatusError(null)
      })
      .catch((e: unknown) => current && setStatusError(errorMessage(e)))
    return () => {
      current = false
    }
  }, [version])

  const built = STEP_BUILT.filter(Boolean).length

  return (
    <div className="shell">
      <aside className="rail">
        <div className="rail-head">
          <p className="rail-stage">Stage 1 of 4</p>
          <h1>
            <span className="rail-o">O1</span> Corpus preparation
          </h1>
          <div
            className="meter"
            role="img"
            aria-label={`${built} of ${STEP_BUILT.length} steps built`}
          >
            {STEP_BUILT.map((isBuilt, index) => (
              <span key={index} className={isBuilt ? 'built' : ''} />
            ))}
          </div>
          <p className="meter-text">
            {built} of {STEP_BUILT.length} steps built
          </p>
        </div>
        <nav className="rail-nav" aria-label="O1 views">
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
      </aside>
      <main className="content" ref={contentRef}>
        {statusError && tab !== 'data' && (
          <p className="message error">Couldn’t read where O1 stands: {statusError}</p>
        )}
        {tab === 'overview' && <Overview status={status} goTo={goTo} />}
        {tab === 'data' && <DataTab status={status} error={statusError} goTo={goTo} />}
        {/* Kept mounted (just hidden) so runs keep being followed while you look at other tabs. */}
        <div className="run-view" hidden={tab !== 'run'}>
          <RunTab status={status} refresh={refresh} />
        </div>
        {tab === 'cases' && <CasesTab key={version} />}
        {tab === 'attributes' && <AttributesTab version={version} />}
        {tab === 'sections' && <SectionsTab key={version} goTo={goTo} />}
        {tab === 'candidates' && <CandidatesTab key={version} goTo={goTo} />}
      </main>
    </div>
  )
}
