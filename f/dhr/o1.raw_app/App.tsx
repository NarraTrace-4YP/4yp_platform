import React, { useCallback, useEffect, useRef, useState } from 'react'
import { backend } from './wmill'
import { Status, message, recall, remember } from './shared'
import Overview from './Overview'
import DataTab from './DataTab'
import RunTab from './RunTab'
import CasesTab from './CasesTab'
import AttributesTab from './AttributesTab'
import SectionsTab from './SectionsTab'
import CandidatesTab from './CandidatesTab'
import MotionCanvas from './MotionCanvas'

const TABS = [
  { id: 'overview', label: 'Overview' },
  { id: 'data', label: 'Data' },
  { id: 'run', label: 'Run' },
  { id: 'cases', label: 'Cases' },
  { id: 'attributes', label: 'Attributes' },
  { id: 'sections', label: 'Sections' },
  { id: 'candidates', label: 'Template candidates' },
]

export default function App() {
  const contentRef = useRef<HTMLElement>(null)
  const headerRef = useRef<HTMLElement>(null)
  const [tab, setTab] = useState(() => recall<string>('tab') ?? 'overview')
  const [headerCollapsed, setHeaderCollapsed] = useState(false)
  const [status, setStatus] = useState<Status | null>(null)
  const [statusError, setStatusError] = useState<string | null>(null)
  // Goes up each time a run finishes, so every tab reloads what it shows.
  const [version, setVersion] = useState(0)

  const goTo = useCallback((id: string) => {
    setTab(id)
    remember('tab', id)
    const behavior = window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'
    requestAnimationFrame(() => contentRef.current?.scrollTo({ top: 0, behavior }))
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

  useEffect(() => {
    const root = contentRef.current
    if (!root) return
    let frame = 0
    let lastScrollTop = root.scrollTop
    const updateProgress = () => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() => {
        const available = root.scrollHeight - root.clientHeight
        root.closest<HTMLElement>('.shell')?.style.setProperty(
          '--app-scroll',
          String(available > 0 ? root.scrollTop / available : 0)
        )
        const scrollTop = root.scrollTop
        if (scrollTop < 48 || scrollTop < lastScrollTop - 2) {
          setHeaderCollapsed(false)
        } else if (scrollTop > lastScrollTop + 2) {
          setHeaderCollapsed(true)
        }
        lastScrollTop = scrollTop
      })
    }
    root.addEventListener('scroll', updateProgress, { passive: true })
    updateProgress()
    return () => {
      cancelAnimationFrame(frame)
      root.removeEventListener('scroll', updateProgress)
    }
  }, [tab])

  useEffect(() => {
    const header = headerRef.current
    if (!header) return
    const updateHeight = () => header.style.setProperty('--app-header-height', `${header.scrollHeight}px`)
    const observer = new ResizeObserver(updateHeight)
    observer.observe(header)
    updateHeight()
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    const root = contentRef.current
    if (!root) return
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const items = Array.from(
      root.querySelectorAll<HTMLElement>('.intro, .card, .step-body, .run-panel, .details > section')
    )
    items.forEach((item, index) => {
      item.classList.add('motion-card')
      item.style.setProperty('--motion-order', String(index % 6))
    })

    const observer = reduceMotion
      ? null
      : new IntersectionObserver(
          (entries) => {
            entries.forEach((entry) => {
              if (!entry.isIntersecting) return
              entry.target.classList.add('is-visible')
              observer?.unobserve(entry.target)
            })
          },
          { root, rootMargin: '0px 0px -8% 0px', threshold: 0.06 }
        )
    items.forEach((item) => (observer ? observer.observe(item) : item.classList.add('is-visible')))

    const move = (event: PointerEvent) => {
      if (reduceMotion) return
      const button = (event.target as Element).closest<HTMLElement>('button.primary, .link-button')
      if (button) {
        const buttonRect = button.getBoundingClientRect()
        button.style.setProperty('--button-x', `${(event.clientX - buttonRect.left - buttonRect.width / 2) * 0.08}px`)
        button.style.setProperty('--button-y', `${(event.clientY - buttonRect.top - buttonRect.height / 2) * 0.12}px`)
      }
      const target = (event.target as Element).closest<HTMLElement>('.motion-card')
      if (!target) return
      const rect = target.getBoundingClientRect()
      const x = (event.clientX - rect.left) / rect.width - 0.5
      const y = (event.clientY - rect.top) / rect.height - 0.5
      target.style.setProperty('--card-rx', `${(-y * 1.6).toFixed(2)}deg`)
      target.style.setProperty('--card-ry', `${(x * 1.6).toFixed(2)}deg`)
      target.style.setProperty('--card-x', `${((x + 0.5) * 100).toFixed(1)}%`)
      target.style.setProperty('--card-y', `${((y + 0.5) * 100).toFixed(1)}%`)
    }
    const leave = (event: PointerEvent) => {
      const button = (event.target as Element).closest<HTMLElement>('button.primary, .link-button')
      if (button && !(event.relatedTarget instanceof Node && button.contains(event.relatedTarget))) {
        button.style.setProperty('--button-x', '0px')
        button.style.setProperty('--button-y', '0px')
      }
      const target = (event.target as Element).closest<HTMLElement>('.motion-card')
      if (!target || (event.relatedTarget instanceof Node && target.contains(event.relatedTarget))) return
      target.style.setProperty('--card-rx', '0deg')
      target.style.setProperty('--card-ry', '0deg')
    }
    root.addEventListener('pointermove', move)
    root.addEventListener('pointerout', leave)
    return () => {
      observer?.disconnect()
      root.removeEventListener('pointermove', move)
      root.removeEventListener('pointerout', leave)
    }
  }, [tab, version, status])

  const moveMagnet = (event: React.PointerEvent<HTMLButtonElement>) => {
    const button = event.currentTarget
    const rect = button.getBoundingClientRect()
    button.style.setProperty('--button-x', `${(event.clientX - rect.left - rect.width / 2) * 0.1}px`)
    button.style.setProperty('--button-y', `${(event.clientY - rect.top - rect.height / 2) * 0.16}px`)
  }

  const resetMagnet = (event: React.PointerEvent<HTMLButtonElement>) => {
    event.currentTarget.style.setProperty('--button-x', '0px')
    event.currentTarget.style.setProperty('--button-y', '0px')
  }

  return (
    <div className="shell">
      <header ref={headerRef} className={headerCollapsed ? 'top is-collapsed' : 'top'}>
        <MotionCanvas />
        <div className="app-scroll-progress" aria-hidden="true"><span /></div>
        <div className="app-header">
          <div className="app-wrap app-header-row">
            <div className="app-heading">
              <h1>O1: Corpus preparation</h1>
            </div>
            <nav className="app-nav" aria-label="O1 views">
              {TABS.map((t) => (
                <button
                  key={t.id}
                  className={t.id === tab ? 'tab selected' : 'tab'}
                  aria-pressed={t.id === tab}
                  onClick={() => goTo(t.id)}
                  onPointerMove={moveMagnet}
                  onPointerLeave={resetMagnet}
                >
                  {t.label}
                </button>
              ))}
            </nav>
          </div>
        </div>
      </header>
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

// test comment for github workflow
