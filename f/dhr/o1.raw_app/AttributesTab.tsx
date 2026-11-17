import React, { useEffect, useMemo, useState } from 'react'
import { backend } from './wmill'
import { DATASETS, message, number, percent, when } from './shared'

// f/dhr/o1_attributes
type Value = { value: string; library: number; datasets: Record<string, number> }
type Totals = { library: number; datasets: Record<string, number> }
// totals: only for "About the review" (CSP, region, years), which every listed review has.
type Attribute = { section: string; attribute: string; values: Value[]; totals?: Totals }
type Attributes = {
  snapshot_id: number | null
  collected_at: string | null
  totals: Totals
  attributes: Attribute[]
}

const key = (a: Attribute) => `${a.section} / ${a.attribute}`
const name = (a: Attribute) => a.attribute || a.section

function Share({ n, total, kind }: { n: number; total: number; kind: string }) {
  return (
    <td className="share">
      <div className="share-bar">
        <span className={kind} style={{ width: total ? `${(100 * n) / total}%` : 0 }} />
      </div>
      <span className="share-text">
        {percent(n, total)} <span className="muted">({number(n)})</span>
      </span>
    </td>
  )
}

export default function AttributesTab({ version }: { version: number }) {
  const [data, setData] = useState<Attributes | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [search, setSearch] = useState('')

  useEffect(() => {
    let current = true
    backend
      .attributes()
      .then((result: Attributes) => {
        if (!current) return
        setData(result)
        setSelected((s) => s ?? (result.attributes[0] ? key(result.attributes[0]) : null))
      })
      .catch((e: unknown) => current && setError(message(e)))
    return () => {
      current = false
    }
  }, [version])

  const sections = useMemo(() => {
    const term = search.trim().toLowerCase()
    const grouped: Record<string, Attribute[]> = {}
    for (const a of data?.attributes ?? []) {
      const matches =
        !term ||
        key(a).toLowerCase().includes(term) ||
        a.values.some((v) => v.value.toLowerCase().includes(term))
      if (matches) (grouped[a.section] ??= []).push(a)
    }
    return Object.entries(grouped)
  }, [data, search])

  if (error) return <div className="message error">Couldn’t count the attributes: {error}</div>
  if (!data) return <div className="message">Counting…</div>
  if (!data.snapshot_id) {
    return <div className="message">No case metadata yet. Run “Collect case metadata” on the Run tab first.</div>
  }

  const attribute = data.attributes.find((a) => key(a) === selected) ?? null
  const totals = attribute?.totals ?? data.totals
  const term = search.trim().toLowerCase()
  const values = attribute
    ? attribute.values.filter(
        (v) => !term || key(attribute).toLowerCase().includes(term) || v.value.toLowerCase().includes(term),
      )
    : []

  return (
    <div className="cases">
      <aside className="list">
        <input
          type="search"
          placeholder="Find an attribute or value"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        {sections.map(([section, attributes]) => (
          <div key={section} className="attr-group">
            <h4>{section}</h4>
            <ul>
              {attributes.map((a) => (
                <li key={key(a)}>
                  <button
                    className={key(a) === selected ? 'review selected' : 'review'}
                    onClick={() => setSelected(key(a))}
                  >
                    <span className="title">{name(a)}</span>
                    <span className="meta">{a.values.length} values</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </aside>
      <main className="details">
        <p className="muted">
          {attribute?.totals ? (
            <>
              Every review’s entry in the library’s results list shows its CSP, CSP region and dates, so these shares
              are out of all the reviews in the library: {number(totals.library)} in the whole library
              {DATASETS.map((d) => `, ${number(totals.datasets[d] ?? 0)} in ${d}`).join('')}.
            </>
          ) : (
            <>
              Shares are out of the reviews with at least one case detail: {number(totals.library)} in the whole
              library{DATASETS.map((d) => `, ${number(totals.datasets[d] ?? 0)} in ${d}`).join('')}. A review that
              wasn’t returned for a value isn’t counted, which can mean “no” or “not recorded”.
            </>
          )}{' '}
          Comparing a dataset with the library shows whether it is representative. From snapshot {data.snapshot_id},
          collected {when(data.collected_at)}.
        </p>
        {attribute && (
          <>
            <h2>
              {attribute.section}
              {attribute.attribute && ` · ${attribute.attribute}`}
            </h2>
            <table className="shares">
              <thead>
                <tr>
                  <th>Value</th>
                  <th>Whole library</th>
                  {DATASETS.map((d) => (
                    <th key={d}>{d}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {values.map((v) => (
                  <tr key={v.value}>
                    <th>{v.value}</th>
                    <Share n={v.library} total={totals.library} kind="library" />
                    {DATASETS.map((d, i) => (
                      <Share key={d} n={v.datasets[d] ?? 0} total={totals.datasets[d] ?? 0} kind={`ds${i}`} />
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="muted">
              A review can have several values of one attribute (several themes, or several victims), so shares can
              add up to more than 100%.
            </p>
          </>
        )}
      </main>
    </div>
  )
}
