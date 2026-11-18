import React, { useEffect, useMemo, useState } from 'react'
import { backend } from './wmill'
import {
  DATASETS,
  HEADINGS,
  Headings,
  errorMessage,
  formatNumber,
  readStoredValue,
  writeStoredValue,
  shortenId,
  formatDateTime,
} from './shared'
import { Block, Blocks, GroupedSection, SectionText, pages, parents } from './SectionText'

// What f/dhr/O1/group_blocks_by_section/get_sections returns.
type Grouped = {
  revision: string
  headings: Headings
  grouped_at: string
  git_commit: string | null
  n_reviews: number
  n_sections: number
  n_blocks: number
  n_skipped: number
}
type ReviewRow = {
  doc_id: string
  n_pages: number
  n_sections: number
  depth: number
  n_across_pages: number
  n_blocks: number
  n_before: number
}
type Overview = {
  dataset: string
  grouped: Grouped | null
  reviews: ReviewRow[]
  skipped: { doc_id: string; problem: string }[]
}
type Review = { doc_id: string; sections: GroupedSection[]; before: Block[] }

const BEFORE = '(before the first heading)'

export default function SectionsTab({ goTo }: { goTo: (tab: string) => void }) {
  const [dataset, setDataset] = useState(() => {
    const saved = readStoredValue<string>('sections:dataset')
    return saved && DATASETS.includes(saved) ? saved : DATASETS[0]
  })
  const [overview, setOverview] = useState<Overview | null>(null)
  const [docId, setDocId] = useState<string | null>(null)
  const [review, setReview] = useState<Review | null>(null)
  const [sectionId, setSectionId] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [error, setError] = useState<string | null>(null)

  function chooseDataset(name: string) {
    if (name === dataset) return
    setDataset(name)
    writeStoredValue('sections:dataset', name)
    setOverview(null)
    setDocId(null)
    setReview(null)
  }

  function chooseReview(id: string) {
    setDocId(id)
    writeStoredValue(`sections:doc:${dataset}`, id)
  }

  useEffect(() => {
    let current = true
    backend
      .sections({ dataset })
      .then((result: Overview) => {
        if (!current) return
        setOverview(result)
        const saved = readStoredValue<string>(`sections:doc:${dataset}`)
        const ids = result.reviews.map((r) => r.doc_id)
        setDocId(saved && ids.includes(saved) ? saved : (ids[0] ?? null))
      })
      .catch((e: unknown) => current && setError(errorMessage(e)))
    return () => {
      current = false
    }
  }, [dataset])

  useEffect(() => {
    if (!docId) return
    let current = true
    setReview(null)
    backend
      .sections({ dataset, doc_id: docId })
      .then((result: Review) => {
        if (!current) return
        setReview(result)
        setSectionId(result.sections[0]?.section_id ?? (result.before.length ? BEFORE : null))
      })
      .catch((e: unknown) => current && setError(errorMessage(e)))
    return () => {
      current = false
    }
  }, [dataset, docId])

  const reviews = useMemo(() => {
    const term = search.trim().toLowerCase()
    return (overview?.reviews ?? []).filter((r) => !term || r.doc_id.toLowerCase().includes(term))
  }, [overview, search])

  const byId = useMemo(
    () => new Map((review?.sections ?? []).map((s) => [s.section_id, s])),
    [review],
  )

  if (error) return <div className="message error">Couldn’t read the sections: {error}</div>

  const row = overview?.reviews.find((r) => r.doc_id === docId)
  const section = sectionId ? byId.get(sectionId) : undefined

  return (
    <div className="three-columns">
      <aside className="list">
        <div className="datasets" role="group" aria-label="Dataset">
          {DATASETS.map((name) => (
            <button
              key={name}
              className={name === dataset ? 'selected' : ''}
              aria-pressed={name === dataset}
              onClick={() => chooseDataset(name)}
            >
              {name}
            </button>
          ))}
        </div>
        {!overview ? (
          <p className="count">Loading…</p>
        ) : !overview.grouped ? (
          <p className="message">
            {dataset} hasn’t been grouped by section yet. Run “Group blocks by section” on the{' '}
            <a onClick={() => goTo('run')}>Run</a> tab.
          </p>
        ) : (
          <>
            <p className="count">
              {formatNumber(overview.grouped.n_reviews)} reviews ·{' '}
              {formatNumber(overview.grouped.n_sections)} sections ·{' '}
              {formatNumber(overview.grouped.n_blocks)} blocks. Grouped{' '}
              {formatDateTime(overview.grouped.grouped_at)} from{' '}
              {HEADINGS[overview.grouped.headings]}, by code{' '}
              {shortenId(overview.grouped.git_commit)}.
            </p>
            {overview.skipped.length > 0 && (
              <details className="skipped">
                <summary>
                  {formatNumber(overview.skipped.length)} review
                  {overview.skipped.length === 1 ? '' : 's'} left out
                </summary>
                <ul>
                  {overview.skipped.map((s) => (
                    <li key={s.doc_id}>
                      <strong>{s.doc_id}</strong>: {s.problem}
                    </li>
                  ))}
                </ul>
              </details>
            )}
            <input
              type="search"
              placeholder="Find a review"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <ul>
              {reviews.map((r) => (
                <li key={r.doc_id}>
                  <button
                    className={r.doc_id === docId ? 'review selected' : 'review'}
                    onClick={() => chooseReview(r.doc_id)}
                  >
                    <span className="title">{r.doc_id}</span>
                    <span className="meta">
                      {formatNumber(r.n_sections)} sections · {formatNumber(r.n_blocks)} blocks ·{' '}
                      {formatNumber(r.n_pages)} pages
                      {r.n_sections === 0 && ' · not grouped'}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
            {reviews.length === 0 && <p className="muted small">No review matches “{search}”.</p>}
          </>
        )}
      </aside>

      <aside className="outline">
        {!docId ? null : !review ? (
          <p className="count">Loading the review…</p>
        ) : (
          <>
            <h3 className="outline-title">{review.doc_id}</h3>
            {row && (
              <p className="count">
                {formatNumber(row.n_sections)} sections, up to {row.depth} level
                {row.depth === 1 ? '' : 's'} deep; {formatNumber(row.n_across_pages)} cross a page
                break.
              </p>
            )}
            <ul>
              {review.before.length > 0 && (
                <li>
                  <button
                    className={sectionId === BEFORE ? 'outline-item selected' : 'outline-item'}
                    onClick={() => setSectionId(BEFORE)}
                  >
                    <span className="heading muted">Before the first heading</span>
                    <span className="meta">{formatNumber(review.before.length)} blocks</span>
                  </button>
                </li>
              )}
              {review.sections.map((s) => (
                <li key={s.section_id}>
                  <button
                    className={
                      s.section_id === sectionId ? 'outline-item selected' : 'outline-item'
                    }
                    style={{ paddingLeft: 8 + (s.level - 1) * 16 }}
                    onClick={() => setSectionId(s.section_id)}
                  >
                    <span className="heading">{s.heading}</span>
                    <span className="meta">
                      {pages(s.first_page, s.last_page)} · {formatNumber(s.n_blocks)} block
                      {s.n_blocks === 1 ? '' : 's'}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
            {review.sections.length === 0 && (
              <p className="muted small">This review has no sections.</p>
            )}
          </>
        )}
      </aside>

      <main className="details">
        {review && sectionId === BEFORE ? (
          <>
            <header>
              <h2>Before the first heading</h2>
              <p className="doc-id">{review.doc_id}</p>
              <p className="muted">
                These blocks come before the review’s first heading (often a title page or a
                contents list), so they aren’t in any section.
              </p>
            </header>
            <Blocks blocks={review.before} top={1} />
          </>
        ) : review && section ? (
          <>
            <header>
              {parents(section, byId).length > 0 && (
                <p className="breadcrumb">
                  {parents(section, byId).map((p) => (
                    <React.Fragment key={p.section_id}>
                      <a onClick={() => setSectionId(p.section_id)}>{p.heading}</a> ›{' '}
                    </React.Fragment>
                  ))}
                </p>
              )}
              <h2>{section.heading}</h2>
              <p className="doc-id">{review.doc_id}</p>
              <dl className="facts">
                <dt>Level</dt>
                <dd>{section.level}</dd>
                <dt>Pages</dt>
                <dd>
                  {pages(section.first_page, section.last_page)}
                  {section.last_page > section.first_page && ' (joined across a page break)'}
                </dd>
                <dt>Blocks</dt>
                <dd>
                  {formatNumber(section.n_blocks)} directly in it
                  {section.n_inside > section.n_blocks &&
                    `, ${formatNumber(section.n_inside)} with its subsections`}
                </dd>
                <dt>Section id</dt>
                <dd>
                  <code>{section.section_id}</code>
                </dd>
              </dl>
            </header>
            <SectionText dataset={dataset} docId={review.doc_id} section={section} />
            <p className="note">
              The section’s text with everything nested inside it, in reading order, as{' '}
              <code>corpus.section_text('{dataset}', doc_id, section_id)</code> gives it. Page
              numbers count from 1 here (the block ids count from 0).
            </p>
          </>
        ) : review ? (
          <div className="message">Pick a section on the left.</div>
        ) : null}
      </main>
    </div>
  )
}
