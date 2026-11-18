import React, { useEffect, useMemo, useState } from 'react'
import { backend } from './wmill'
import { DATASETS, LIBRARY, errorMessage, readStoredValue, writeStoredValue } from './shared'

// Responses from the metadata stage's get_reviews and get_case_details scripts.
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
  tags: number[] // ids of the facet values this review has
}
type FacetValue = { id: number; value: string }
type Facet = { section: string; attribute: string; values: FacetValue[] }
type ReviewList = {
  dataset: string
  snapshot_id: number | null
  collected_at: string | null
  facets: Facet[]
  reviews: Review[]
}
type Section = { section: string; attributes: { attribute: string; values: string[] }[] }
type Details = Omit<Review, 'n_details' | 'tags'> & { sections: Section[] }

// A filter is kept by name (not id), so it survives switching dataset or reloading the page.
type Mode = 'include' | 'exclude'
type Filters = Record<string, Mode>
const SEP = '\u0000'
const keyOf = (section: string, attribute: string, value: string) =>
  [section, attribute, value].join(SEP)
const facetKey = (f: Facet) => `${f.section}${SEP}${f.attribute}`
const label = (f: Facet) => f.attribute || f.section

function matches(text: string | null | undefined, search: string): boolean {
  return (text ?? '').toLowerCase().includes(search)
}

function csvCell(value: unknown): string {
  const text = value === null || value === undefined ? '' : String(value)
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
}

function download(name: string, reviews: Review[], dataset: string) {
  const columns: [string, (r: Review) => unknown][] = [
    ['doc_id', (r) => r.doc_id],
    ['title', (r) => r.title],
    ['dataset', () => dataset],
    ['published_on', (r) => r.source],
    ['in_dhr_library', (r) => (r.in_library ? 'yes' : 'no')],
    ['csp', (r) => r.csp],
    ['csp_region', (r) => r.csp_region],
    ['date_of_death', (r) => r.date_of_death],
    ['uploaded', (r) => r.uploaded],
    ['case_details', (r) => r.n_details],
    [
      'library_link',
      (r) => (r.in_library && r.library_id ? `${LIBRARY}/download/${r.library_id}` : r.source_url),
    ],
  ]
  const lines = [
    columns.map(([c]) => c).join(','),
    ...reviews.map((r) => columns.map(([, get]) => csvCell(get(r))).join(',')),
  ]
  const url = URL.createObjectURL(new Blob([lines.join('\n') + '\n'], { type: 'text/csv' }))
  const link = document.createElement('a')
  link.href = url
  link.download = name
  document.body.appendChild(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export default function CasesTab() {
  const [dataset, setDataset] = useState(() => {
    const saved = readStoredValue<string>('cases:dataset')
    return saved && DATASETS.includes(saved) ? saved : DATASETS[0]
  })
  const [list, setList] = useState<ReviewList | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [details, setDetails] = useState<Details | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [reviewSearch, setReviewSearch] = useState('')
  const [detailSearch, setDetailSearch] = useState('')
  const [filterSearch, setFilterSearch] = useState('')
  const [showEmpty, setShowEmpty] = useState(false)
  const [filters, setFilters] = useState<Filters>(
    () => readStoredValue<Filters>('cases:filters') ?? {},
  )

  function chooseDataset(name: string) {
    if (name === dataset) return
    setDataset(name)
    writeStoredValue('cases:dataset', name)
    setList(null)
    setSelected(null)
    setDetails(null)
  }

  function changeFilters(next: Filters) {
    setFilters(next)
    writeStoredValue('cases:filters', next)
  }

  function toggle(key: string, mode: Mode) {
    const next = { ...filters }
    if (next[key] === mode) delete next[key]
    else next[key] = mode
    changeFilters(next)
  }

  useEffect(() => {
    let current = true
    backend
      .reviews({ dataset })
      .then((result: ReviewList) => {
        if (!current) return
        setList({ ...result, facets: result.facets ?? [] })
        if (result.reviews.length > 0) setSelected(result.reviews[0].doc_id)
      })
      .catch((e: unknown) => current && setError(errorMessage(e)))
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
      .catch((e: unknown) => current && setError(errorMessage(e)))
      .finally(() => current && setLoading(false))
    return () => {
      current = false
    }
  }, [dataset, selected])

  // Lookups built once per dataset: each value's id and facet, each review's tags and words.
  const index = useMemo(() => {
    const idOf = new Map<string, number>()
    const facetOf = new Map<number, number>() // value id -> facet position
    const valueOf = new Map<number, FacetValue>()
    list?.facets.forEach((f, i) =>
      f.values.forEach((v) => {
        idOf.set(keyOf(f.section, f.attribute, v.value), v.id)
        facetOf.set(v.id, i)
        valueOf.set(v.id, v)
      }),
    )
    const tags = new Map<string, Set<number>>()
    const words = new Map<string, string>()
    for (const r of list?.reviews ?? []) {
      tags.set(r.doc_id, new Set(r.tags ?? []))
      const values = (r.tags ?? []).map((id) => valueOf.get(id)?.value ?? '')
      words.set(
        r.doc_id,
        [r.title, r.doc_id, r.csp, r.csp_region, ...values].join('\n').toLowerCase(),
      )
    }
    return { idOf, facetOf, tags, words }
  }, [list])

  // The active filters, grouped by facet: within one facet any included value will do, and
  // no excluded value may be present; every facet with filters must be satisfied.
  const groups = useMemo(() => {
    const byFacet = new Map<string, { include: (number | null)[]; exclude: number[] }>()
    for (const [key, mode] of Object.entries(filters)) {
      const [section, attribute] = key.split(SEP)
      const group = byFacet.get(`${section}${SEP}${attribute}`) ?? { include: [], exclude: [] }
      const id = index.idOf.get(key) ?? null // null: this dataset has no such value
      if (mode === 'include') group.include.push(id)
      else if (id !== null) group.exclude.push(id)
      byFacet.set(`${section}${SEP}${attribute}`, group)
    }
    return byFacet
  }, [filters, index])

  const search = reviewSearch.trim().toLowerCase()

  function passes(r: Review, skipFacet?: string): boolean {
    if (search && !(index.words.get(r.doc_id) ?? '').includes(search)) return false
    const tags = index.tags.get(r.doc_id) ?? new Set<number>()
    for (const [facet, group] of groups) {
      if (facet === skipFacet) continue
      if (group.include.length && !group.include.some((id) => id !== null && tags.has(id)))
        return false
      if (group.exclude.some((id) => tags.has(id))) return false
    }
    return true
  }

  const reviews = useMemo(
    () => (list ? list.reviews.filter((r) => passes(r)) : []),
    [list, index, groups, search],
  )

  // When the filters leave out the review being shown, show the first one that's left.
  useEffect(() => {
    if (reviews.length > 0 && !reviews.some((r) => r.doc_id === selected))
      setSelected(reviews[0].doc_id)
  }, [reviews, selected])

  // How many reviews each value would give: out of the current results, except that for a facet
  // with filters of its own, out of the results without that facet's filters (so you can see what
  // adding another value would bring in).
  const counts = useMemo(() => {
    const result = new Map<number, number>()
    if (!list) return result
    const filtered = list.facets.map((f) => groups.has(facetKey(f)))
    const add = (pool: Review[], facetIndex?: number) => {
      for (const r of pool) {
        for (const id of index.tags.get(r.doc_id) ?? []) {
          const own = index.facetOf.get(id) ?? -1
          if (facetIndex === undefined ? filtered[own] : own !== facetIndex) continue
          result.set(id, (result.get(id) ?? 0) + 1)
        }
      }
    }
    add(reviews)
    list.facets.forEach((f, i) => {
      if (filtered[i])
        add(
          list.reviews.filter((r) => passes(r, facetKey(f))),
          i,
        )
    })
    return result
  }, [list, index, groups, reviews, search])

  const facetSearch = filterSearch.trim().toLowerCase()
  const shownFacets = useMemo(() => {
    if (!list) return []
    return list.facets
      .map((f) => {
        const active = groups.has(facetKey(f))
        const facetMatches = !facetSearch || matches(`${f.section} ${f.attribute}`, facetSearch)
        const values = f.values.filter(
          (v) =>
            filters[keyOf(f.section, f.attribute, v.value)] ||
            ((showEmpty || (counts.get(v.id) ?? 0) > 0) &&
              (facetMatches || matches(v.value, facetSearch))),
        )
        return { facet: f, values, active }
      })
      .filter((x) => x.values.length > 0)
  }, [list, filters, counts, facetSearch, showEmpty, groups])

  const sections = useMemo(() => {
    const term = detailSearch.trim().toLowerCase()
    if (!details) return []
    if (!term) return details.sections
    return details.sections
      .map((s) => ({
        ...s,
        attributes: s.attributes
          .map((a) => ({
            ...a,
            values: matches(a.attribute, term)
              ? a.values
              : a.values.filter((v) => matches(v, term)),
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

  const active = Object.entries(filters)
  const inLibrary = list ? list.reviews.filter((r) => r.in_library).length : 0
  let previousSection = ''

  return (
    <div className="explorer">
      <aside className="filters">
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
          placeholder="Search names, CSPs and case details"
          value={reviewSearch}
          onChange={(e) => setReviewSearch(e.target.value)}
        />

        <div className="active-filters">
          {active.length === 0 ? (
            <p className="muted small">No filters yet: pick values below.</p>
          ) : (
            <>
              {active.map(([key, mode]) => {
                const [section, attribute, value] = key.split(SEP)
                return (
                  <button
                    key={key}
                    className={`filter-chip ${mode}`}
                    title="Remove this filter"
                    onClick={() => toggle(key, mode)}
                  >
                    {mode === 'exclude' && <strong>not </strong>}
                    {attribute || section}: {value} ✕
                  </button>
                )
              })}
              <button className="link-button" onClick={() => changeFilters({})}>
                Clear all
              </button>
            </>
          )}
        </div>

        <input
          type="search"
          placeholder="Find a filter (e.g. strangulation, Kent, HALT)"
          value={filterSearch}
          onChange={(e) => setFilterSearch(e.target.value)}
        />
        <label className="small muted">
          <input
            type="checkbox"
            checked={showEmpty}
            onChange={(e) => setShowEmpty(e.target.checked)}
          />{' '}
          Show values no review here has
        </label>

        {!list ? (
          <p className="count">Loading…</p>
        ) : (
          <div className="facets">
            {shownFacets.map(({ facet, values, active: facetActive }) => {
              const heading = facet.section !== previousSection ? facet.section : null
              previousSection = facet.section
              return (
                <React.Fragment key={facetKey(facet)}>
                  {heading && <h4>{heading}</h4>}
                  <details className="facet" open={facetActive || Boolean(facetSearch)}>
                    <summary>
                      <span>{label(facet)}</span>
                      <span className="muted small">{values.length}</span>
                    </summary>
                    <ul>
                      {values.map((v) => {
                        const key = keyOf(facet.section, facet.attribute, v.value)
                        const mode = filters[key]
                        return (
                          <li key={v.id} className={mode ?? ''}>
                            <button
                              className="facet-value"
                              aria-pressed={mode === 'include'}
                              onClick={() => toggle(key, 'include')}
                            >
                              <span>{v.value}</span>
                              <span className="muted small">{counts.get(v.id) ?? 0}</span>
                            </button>
                            <button
                              className="facet-not"
                              aria-pressed={mode === 'exclude'}
                              title="Leave out reviews with this"
                              onClick={() => toggle(key, 'exclude')}
                            >
                              not
                            </button>
                          </li>
                        )
                      })}
                    </ul>
                  </details>
                </React.Fragment>
              )
            })}
            {shownFacets.length === 0 && (
              <p className="muted small">No filter matches “{filterSearch}”.</p>
            )}
          </div>
        )}
        <p className="note small">
          Pick several values of one attribute to find reviews with any of them; filters on
          different attributes must all hold. A case detail a review doesn’t have means the library
          didn’t return it for that search, which can be “no” or “not recorded”, so “not” leaves out
          only the reviews the library did return.
        </p>
      </aside>

      <aside className="list">
        {!list ? (
          <p className="count">Loading the reviews…</p>
        ) : (
          <>
            <p className="count">
              {reviews.length} of {list.reviews.length} reviews
              {inLibrary < list.reviews.length && ` · ${inLibrary} in the DHR Library`}
            </p>
            {reviews.length > 0 && (
              <button
                className="link-button small"
                onClick={() => download(`${dataset}-cases.csv`, reviews, dataset)}
              >
                Download this list (CSV)
              </button>
            )}
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
                      {r.in_library
                        ? `${r.n_details} details`
                        : `not in the library (${r.source ?? 'no link'})`}
                      {!r.loaded && ' · skipped when loading'}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
            {reviews.length === 0 && (
              <p className="muted small">No review matches these filters.</p>
            )}
          </>
        )}
      </aside>

      <main className="details">
        {list && list.reviews.length === 0 ? (
          <div className="message">
            No {dataset} reviews are loaded yet. Run <code>f/dhr/O1/load_reviews/load_reviews</code>{' '}
            with dataset <code>{dataset}</code> first.
          </div>
        ) : !details ? (
          <div className="message">Loading…</div>
        ) : (
          <>
            {list && list.snapshot_id === null && (
              <p className="message">
                No case details have been collected yet. Run{' '}
                <code>f/dhr/O1/link_case_metadata/link_case_metadata</code> in full to add them.
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
                <dt>Published on</dt>
                <dd>{details.source ?? '—'}</dd>
              </dl>
              {details.in_library && details.library_id ? (
                <a
                  href={`${LIBRARY}/download/${details.library_id}`}
                  target="_blank"
                  rel="noreferrer"
                >
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
                                <span
                                  key={v}
                                  className={
                                    filters[keyOf(s.section, a.attribute, v)] === 'include'
                                      ? 'value matched'
                                      : 'value'
                                  }
                                >
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
              "not recorded". The library warns its tags may contain mistakes. Details your filters
              picked are highlighted.
              {dataset === 'DHRC-RAW' &&
                ' DHRC-RAW reviews are raw automated output that hasn’t been checked by hand.'}
            </p>
          </>
        )}
      </main>
    </div>
  )
}
