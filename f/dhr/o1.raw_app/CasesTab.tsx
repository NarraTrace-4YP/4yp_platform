import React, { useEffect, useMemo, useState } from 'react'
import { backend } from './wmill'
import { DATASETS, LIBRARY, message } from './shared'

// What the two backend scripts return (f/dhr/case_viewer_reviews and case_viewer_details).
type Review = {
  doc_id: string
  library_id: string | null
  title: string | null
  csp: string | null
  csp_region: string | null
  date_of_death: string | null
  uploaded: string | null
  in_library: boolean
  source_url: string | null
  loaded: boolean // false: the review's file couldn't be read, so it was skipped when loading
  source: string | null // where it was published: 'Home Office website', 'HALT' or 'Elsewhere'
  n_details: number
}
type ReviewList = {
  dataset: string
  snapshot_id: number | null
  collected_at: string | null
  reviews: Review[]
}
type Section = { section: string; attributes: { attribute: string; values: string[] }[] }
type Details = Omit<Review, 'n_details'> & { sections: Section[] }

function matches(text: string | null | undefined, search: string): boolean {
  return (text ?? '').toLowerCase().includes(search)
}

export default function CasesTab() {
  const [dataset, setDataset] = useState(DATASETS[0])
  const [list, setList] = useState<ReviewList | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [details, setDetails] = useState<Details | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [reviewSearch, setReviewSearch] = useState('')
  const [detailSearch, setDetailSearch] = useState('')

  function chooseDataset(name: string) {
    if (name === dataset) return
    setDataset(name)
    setList(null)
    setSelected(null)
    setDetails(null)
  }

  useEffect(() => {
    let current = true
    backend
      .reviews({ dataset })
      .then((result: ReviewList) => {
        if (!current) return
        setList(result)
        if (result.reviews.length > 0) setSelected(result.reviews[0].doc_id)
      })
      .catch((e: unknown) => current && setError(message(e)))
    return () => {
      current = false
    }
  }, [dataset])

  useEffect(() => {
    if (!selected) return
    let current = true
    setLoading(true)
    backend
      .details({ dataset, doc_id: selected })
      .then((result: Details) => current && setDetails(result))
      .catch((e: unknown) => current && setError(message(e)))
      .finally(() => current && setLoading(false))
    return () => {
      current = false
    }
  }, [dataset, selected])

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

  const inLibrary = list ? list.reviews.filter((r) => r.in_library).length : 0

  return (
    <div className="cases">
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
        <input
          type="search"
          placeholder="Search by name, CSP or region"
          value={reviewSearch}
          onChange={(e) => setReviewSearch(e.target.value)}
        />
        {!list ? (
          <p className="count">Loading the reviews…</p>
        ) : (
          <>
            <p className="count">
              {reviews.length} of {list.reviews.length} reviews
              {inLibrary < list.reviews.length && ` · ${inLibrary} in the DHR Library`}
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
                      {r.csp ?? 'CSP not listed'} · {r.date_of_death ?? 'date unknown'} ·{' '}
                      {r.in_library ? `${r.n_details} details` : `not in the library (${r.source ?? 'no link'})`}
                      {!r.loaded && ' · skipped when loading'}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}
      </aside>

      <main className="details">
        {list && list.reviews.length === 0 ? (
          <div className="message">
            No {dataset} reviews are loaded yet. Run <code>f/dhr/load_dhrc</code> with dataset{' '}
            <code>{dataset}</code> first.
          </div>
        ) : !details ? (
          <div className="message">Loading…</div>
        ) : (
          <>
            {list && list.snapshot_id === null && (
              <p className="message">
                No case details have been collected yet. Run <code>f/dhr/collect_case_metadata</code> in
                full to add them.
              </p>
            )}
            <header className={loading ? 'faded' : ''}>
              <h2>{details.title ?? details.doc_id}</h2>
              <p className="doc-id">
                {details.doc_id} · {dataset}
              </p>
              {!details.loaded && (
                <p className="warning">
                  This review's file couldn't be read when {dataset} was loaded, so its text isn't
                  in the database. The load's log lists why.
                </p>
              )}
              <dl className="facts">
                <dt>CSP</dt>
                <dd>{details.csp ?? '—'}</dd>
                <dt>Region</dt>
                <dd>{details.csp_region ?? '—'}</dd>
                <dt>Date of death</dt>
                <dd>{details.date_of_death ?? '—'}</dd>
                <dt>Uploaded</dt>
                <dd>{details.uploaded ?? '—'}</dd>
                <dt>Source</dt>
                <dd>{details.source ?? '—'}</dd>
              </dl>
              {details.in_library && details.library_id ? (
                <a href={`${LIBRARY}/download/${details.library_id}`} target="_blank" rel="noreferrer">
                  Open the review in the DHR Library ↗
                </a>
              ) : (
                details.source_url && (
                  <a href={details.source_url} target="_blank" rel="noreferrer">
                    {details.source === 'HALT'
                      ? 'Open the review in the HALT repository ↗'
                      : 'Open the review where it was published ↗'}
                  </a>
                )
              )}
            </header>

            {!details.in_library ? (
              <p className="message">
                This review isn't in the Home Office DHR Library, so the library has no case details
                for it. The facts above come from the dataset's own list of reviews.
              </p>
            ) : (
              <>
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
              </>
            )}

            <p className="note">
              Case details are from the Home Office DHR Library
              {list?.collected_at ? `, collected ${list.collected_at.slice(0, 10)}` : ''}. A detail
              that isn't shown means the library didn't return this review for it: either "no" or
              "not recorded". The library warns its tags may contain mistakes.
              {dataset === 'DHRC-RAW' &&
                ' DHRC-RAW reviews are raw automated output that hasn’t been checked by hand.'}
            </p>
          </>
        )}
      </main>
    </div>
  )
}
