import React, { useEffect, useMemo, useState } from 'react'
import { backend } from './wmill'

// What the two backend scripts return (f/dhr/case_viewer_reviews and case_viewer_details).
type Review = {
  doc_id: string
  library_id: string | null
  title: string | null
  csp: string | null
  csp_region: string | null
  date_of_death: string | null
  uploaded: string | null
  n_details: number
}
type ReviewList = { snapshot_id: number | null; collected_at: string | null; reviews: Review[] }
type Section = { section: string; attributes: { attribute: string; values: string[] }[] }
type Details = Omit<Review, 'n_details'> & { sections: Section[] }

const LIBRARY = 'https://homicide-review.homeoffice.gov.uk'

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function matches(text: string | null | undefined, search: string): boolean {
  return (text ?? '').toLowerCase().includes(search)
}

export default function App() {
  const [list, setList] = useState<ReviewList | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [details, setDetails] = useState<Details | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [reviewSearch, setReviewSearch] = useState('')
  const [detailSearch, setDetailSearch] = useState('')

  useEffect(() => {
    document.title = 'DHR case viewer'
    backend
      .reviews()
      .then((result: ReviewList) => {
        setList(result)
        if (result.reviews.length > 0) setSelected(result.reviews[0].doc_id)
      })
      .catch((e: unknown) => setError(message(e)))
  }, [])

  useEffect(() => {
    if (!selected) return
    let current = true
    setLoading(true)
    backend
      .details({ doc_id: selected })
      .then((result: Details) => current && setDetails(result))
      .catch((e: unknown) => current && setError(message(e)))
      .finally(() => current && setLoading(false))
    return () => {
      current = false
    }
  }, [selected])

  const reviews = useMemo(() => {
    const search = reviewSearch.trim().toLowerCase()
    if (!list) return []
    if (!search) return list.reviews
    return list.reviews.filter(
      (r) =>
        matches(r.title, search) ||
        matches(r.doc_id, search) ||
        matches(r.csp, search) ||
        matches(r.csp_region, search),
    )
  }, [list, reviewSearch])

  const sections = useMemo(() => {
    const search = detailSearch.trim().toLowerCase()
    if (!details) return []
    if (!search) return details.sections
    return details.sections
      .map((s) => ({
        ...s,
        attributes: s.attributes
          .map((a) => ({
            ...a,
            values: matches(a.attribute, search) ? a.values : a.values.filter((v) => matches(v, search)),
          }))
          .filter((a) => a.values.length > 0),
      }))
      .filter((s) => s.attributes.length > 0)
  }, [details, detailSearch])

  if (error) {
    return (
      <div className="message error">
        <strong>Something went wrong.</strong> {error}
      </div>
    )
  }
  if (!list) return <div className="message">Loading the reviews…</div>
  if (list.reviews.length === 0) {
    return (
      <div className="message">
        No case details yet. Run <code>f/dhr/collect_case_metadata</code> in full first.
      </div>
    )
  }

  return (
    <div className="app">
      <aside className="list">
        <h1>DHR case viewer</h1>
        <input
          type="search"
          placeholder="Search by name, CSP or region"
          value={reviewSearch}
          onChange={(e) => setReviewSearch(e.target.value)}
        />
        <p className="count">
          {reviews.length} of {list.reviews.length} reviews
        </p>
        <ul>
          {reviews.map((r) => (
            <li key={r.doc_id}>
              <button
                className={r.doc_id === selected ? 'review selected' : 'review'}
                onClick={() => setSelected(r.doc_id)}
              >
                <span className="title">{r.title ?? r.doc_id}</span>
                <span className="meta">
                  {r.csp ?? 'CSP not listed'} · {r.date_of_death ?? 'date unknown'} · {r.n_details} details
                </span>
              </button>
            </li>
          ))}
        </ul>
      </aside>

      <main className="details">
        {!details ? (
          <div className="message">Loading…</div>
        ) : (
          <>
            <header className={loading ? 'faded' : ''}>
              <h2>{details.title ?? details.doc_id}</h2>
              <p className="doc-id">{details.doc_id}</p>
              <dl className="facts">
                <dt>CSP</dt>
                <dd>{details.csp ?? '—'}</dd>
                <dt>Region</dt>
                <dd>{details.csp_region ?? '—'}</dd>
                <dt>Date of death</dt>
                <dd>{details.date_of_death ?? '—'}</dd>
                <dt>Uploaded</dt>
                <dd>{details.uploaded ?? '—'}</dd>
              </dl>
              {details.library_id && (
                <a href={`${LIBRARY}/download/${details.library_id}`} target="_blank" rel="noreferrer">
                  Open the review in the DHR Library ↗
                </a>
              )}
            </header>

            <input
              type="search"
              placeholder="Find a detail (e.g. strangulation, carer, Kent)"
              value={detailSearch}
              onChange={(e) => setDetailSearch(e.target.value)}
            />

            {details.sections.length === 0 && (
              <p className="message">The library returned no case details for this review.</p>
            )}
            {details.sections.length > 0 && sections.length === 0 && (
              <p className="message">No details match “{detailSearch}”.</p>
            )}

            {sections.map((s) => (
              <section key={s.section} className={loading ? 'faded' : ''}>
                <h3>{s.section}</h3>
                <table>
                  <tbody>
                    {s.attributes.map((a) => (
                      <tr key={a.attribute}>
                        {a.attribute && <th>{a.attribute}</th>}
                        <td colSpan={a.attribute ? 1 : 2}>
                          {a.values.map((v) => (
                            <span key={v} className="value">
                              {v}
                            </span>
                          ))}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
            ))}

            <p className="note">
              From the Home Office DHR Library
              {list.collected_at ? `, collected ${list.collected_at.slice(0, 10)}` : ''}. A detail that
              isn't shown means the library didn't return this review for it: either "no" or "not
              recorded". The library warns its tags may contain mistakes.
            </p>
          </>
        )}
      </main>
    </div>
  )
}
