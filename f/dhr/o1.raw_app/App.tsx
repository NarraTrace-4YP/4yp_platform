import React, { useCallback, useEffect, useState } from 'react'
import { backend } from './wmill'
import { Status, message, recall, remember } from './shared'
import Overview from './Overview'
import DataTab from './DataTab'
import RunTab from './RunTab'
import CasesTab from './CasesTab'
import AttributesTab from './AttributesTab'

const TABS = [
  { id: 'overview', label: 'Overview' },
  { id: 'data', label: 'Data' },
  { id: 'run', label: 'Run' },
  { id: 'cases', label: 'Cases' },
  { id: 'attributes', label: 'Attributes' },
]

export default function App() {
  const [tab, setTab] = useState(() => recall<string>('tab') ?? 'overview')
  const [status, setStatus] = useState<Status | null>(null)
  const [statusError, setStatusError] = useState<string | null>(null)
  // Goes up each time a run finishes, so every tab reloads what it shows.
  const [version, setVersion] = useState(0)

  const goTo = useCallback((id: string) => {
    setTab(id)
    remember('tab', id)
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
      .catch((e: unknown) => current && setStatusError(message(e)))
    return () => {
      current = false
    }
  }, [version])

  return (
    <div className="shell">
      <header className="top">
        <div>
          <h1>O1: Corpus preparation</h1>
          <p>Turning the DHR corpus into a hierarchical, standardised database linked to the Home Office case metadata</p>
        </div>
        <nav>
          {TABS.map((t) => (
            <button key={t.id} className={t.id === tab ? 'tab selected' : 'tab'} onClick={() => goTo(t.id)}>
              {t.label}
            </button>
          ))}
        </nav>
      </header>
      <div className="content">
        {statusError && tab !== 'data' && (
          <p className="message error">Couldn’t read where O1 stands: {statusError}</p>
        )}
        {tab === 'overview' && <Overview status={status} goTo={goTo} />}
        {tab === 'data' && <DataTab status={status} error={statusError} goTo={goTo} />}
        {/* Kept mounted (just hidden) so runs keep being followed while you look at other tabs. */}
        <div style={{ display: tab === 'run' ? 'block' : 'none' }}>
          <RunTab status={status} refresh={refresh} />
        </div>
        {tab === 'cases' && <CasesTab key={version} />}
        {tab === 'attributes' && <AttributesTab version={version} />}
      </div>
    </div>
  )
}

// test comment for github workflow
