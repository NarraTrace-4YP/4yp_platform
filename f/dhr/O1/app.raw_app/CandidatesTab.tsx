import React, { useEffect, useMemo, useRef, useState } from 'react'
import { backend } from './wmill'
import {
  DATASETS,
  GUIDANCES,
  errorMessage,
  formatNumber,
  readStoredValue,
  writeStoredValue,
  shortenId,
  formatDateTime,
} from './shared'
import { Section, SectionText, pages, parents } from './SectionText'

// What f/dhr/O1/find_candidate_template_sections/get_candidates returns.
type Run = {
  revision: string
  model: string
  k: number
  min_heading_score: number
  matched_at: string
  git_commit: string | null
  n_sections: number
  n_candidates: number
}
type TemplateSection = {
  position: number
  heading: string
  printed: boolean // false: the guidance gives no heading here, so it was named by us
  guidance: string // what the template says goes there
  page: number | null // as printed in the guidance; none for a Word file
  n_best_by_text: number
  n_best_by_heading: number
  n_best_by_both: number
}
type Template = {
  template: string
  appendix: number
  title: string
  kind: string
  source_url: string | null
  sections: TemplateSection[]
}
type ReviewRow = { doc_id: string; n_sections: number; n_heading_match: number; n_agree: number }
type Overview = {
  dataset: string
  guidance: string
  title?: string
  source_url?: string | null
  run: Run | null
  templates?: Template[]
  reviews?: ReviewRow[]
}
type Scores = {
  heading_score: number
  heading_rank: number
  text_score: number
  text_rank: number
  best_by_text: boolean
  best_by_heading: boolean
}
type Candidate = Scores & {
  template: string
  template_position: number
  template_heading: string
  found_by: 'text' | 'heading' | 'both'
}
type CandidateSection = Section & { candidates: Candidate[] }
type Review = { doc_id: string; sections: CandidateSection[] }
type Match = Scores & {
  doc_id: string
  section_id: string
  level: number
  heading: string
  position: number
}
type Matches = { template: string; template_position: number; n_matches: number; matches: Match[] }

type Mode = 'reviews' | 'templates'
const MODES: [Mode, string][] = [
  ['reviews', 'Reviews'],
  ['templates', 'Template sections'],
]

const keyOf = (template: string, position: number) => `${template}#${position}`

// 'dhr-2016/imr-outline' -> 'IMR outline'
function templateName(template: string): string {
  const words = (template.split('/')[1] ?? template).split('-')
  const named = words.map((w) => (w === 'imr' || w === 'dardr' ? w.toUpperCase() : w)).join(' ')
  return named.charAt(0).toUpperCase() + named.slice(1)
}

function bestBy(s: Scores): string {
  return s.best_by_text && s.best_by_heading
    ? 'both'
    : s.best_by_text
      ? 'text'
      : s.best_by_heading
        ? 'heading'
        : ''
}

function Toggle<T extends string>({
  options,
  value,
  choose,
  label,
}: {
  options: [T, string][]
  value: T
  choose: (v: T) => void
  label: string
}) {
  return (
    <div className="datasets" role="group" aria-label={label}>
      {options.map(([v, text]) => (
        <button
          key={v}
          className={v === value ? 'selected' : ''}
          aria-pressed={v === value}
          onClick={() => choose(v)}
        >
          {text}
        </button>
      ))}
    </div>
  )
}

// A one-line summary of a section's best matches, for the outline.
function BestMatches({ section }: { section: CandidateSection }) {
  const label = (c: Candidate) => `${c.template_heading} (${templateName(c.template)})`
  const more = (n: number) => (n > 1 ? ` +${n - 1} equal` : '')
  const both = section.candidates.filter((c) => c.best_by_text && c.best_by_heading)
  if (both.length) {
    return (
      <span className="match agree">
        ✓ {label(both[0])}
        {more(both.length)}
      </span>
    )
  }
  const heading = section.candidates.filter((c) => c.best_by_heading)
  const text = section.candidates.filter((c) => c.best_by_text)
  return (
    <>
      {heading.length > 0 && (
        <span className="match">
          Heading: {label(heading[0])}
          {more(heading.length)}
        </span>
      )}
      {text.length > 0 && (
        <span className="match">
          Text: {label(text[0])}
          {more(text.length)}
        </span>
      )}
    </>
  )
}

function TemplateText({ template, section }: { template: Template; section: TemplateSection }) {
  return (
    <div className="template-text">
      <p className="muted small">
        {template.title}
        {section.page !== null ? `, page ${section.page} of the guidance` : ', from its Word file'}
        {!section.printed && ' · no heading in the guidance: named here after what goes in it'}
      </p>
      {section.guidance ? (
        <p className="guidance-text">{section.guidance}</p>
      ) : (
        <p className="muted">The template gives only the heading here.</p>
      )}
    </div>
  )
}

export default function CandidatesTab() {
  const [dataset, setDataset] = useState(() => {
    const saved = readStoredValue<string>('candidates:dataset')
    return saved && DATASETS.includes(saved) ? saved : DATASETS[0]
  })
  const [guidance, setGuidance] = useState(() => {
    const saved = readStoredValue<string>('candidates:guidance')
    return saved && saved in GUIDANCES ? saved : Object.keys(GUIDANCES)[0]
  })
  const [mode, setMode] = useState<Mode>(
    () => readStoredValue<Mode>('candidates:mode') ?? 'reviews',
  )
  const [overview, setOverview] = useState<Overview | null>(null)
  const [docId, setDocId] = useState<string | null>(null)
  const [review, setReview] = useState<Review | null>(null)
  const [sectionId, setSectionId] = useState<string | null>(null)
  const [candidateKey, setCandidateKey] = useState<string | null>(null)
  const [templateKey, setTemplateKey] = useState<string | null>(null)
  const [matches, setMatches] = useState<Matches | null>(null)
  const [showText, setShowText] = useState(
    () => readStoredValue<boolean>('candidates:text') ?? false,
  )
  const [search, setSearch] = useState('')
  const [error, setError] = useState<string | null>(null)
  // The section to show once a review has (re)loaded: the one clicked in a list of matches,
  // or the one being shown before (so switching guidance or coming back keeps it).
  const wanted = useRef<string | null>(null)

  const savedDoc = `candidates:doc:${dataset}`

  function choose(next: { dataset?: string; guidance?: string }) {
    if (next.dataset) {
      setDataset(next.dataset)
      writeStoredValue('candidates:dataset', next.dataset)
      wanted.current = null // another dataset's sections have other ids
    }
    if (next.guidance) {
      setGuidance(next.guidance)
      writeStoredValue('candidates:guidance', next.guidance)
    }
    setOverview(null)
    setReview(null)
    setMatches(null)
  }

  function chooseMode(next: Mode) {
    if (next !== mode) setSearch('') // the search box finds reviews in one, template sections in the other
    setMode(next)
    writeStoredValue('candidates:mode', next)
  }

  function chooseReview(id: string) {
    if (id !== docId) wanted.current = null
    setDocId(id)
    writeStoredValue(savedDoc, id)
  }

  function chooseTemplateSection(key: string) {
    setTemplateKey(key)
    writeStoredValue('candidates:template', key)
  }

  function openMatch(m: Match) {
    chooseReview(m.doc_id)
    wanted.current = m.section_id
    chooseMode('reviews') // the review (re)loads, then opens this section
  }

  function selectSection(id: string, r: Review) {
    wanted.current = id
    setSectionId(id)
    const s = r.sections.find((x) => x.section_id === id)
    const pick =
      s?.candidates.find((c) => c.best_by_text && c.best_by_heading) ??
      s?.candidates.find((c) => c.best_by_heading) ??
      s?.candidates[0]
    setCandidateKey(pick ? keyOf(pick.template, pick.template_position) : null)
  }

  useEffect(() => {
    let current = true
    backend
      .candidates({ dataset, guidance })
      .then((result: Overview) => {
        if (!current) return
        setOverview(result)
        const ids = (result.reviews ?? []).map((r) => r.doc_id)
        setDocId((id) => {
          const saved = readStoredValue<string>(`candidates:doc:${dataset}`)
          return id && ids.includes(id)
            ? id
            : saved && ids.includes(saved)
              ? saved
              : (ids[0] ?? null)
        })
        const keys = (result.templates ?? []).flatMap((t) =>
          t.sections.map((s) => keyOf(t.template, s.position)),
        )
        const savedKey = readStoredValue<string>('candidates:template')
        setTemplateKey(savedKey && keys.includes(savedKey) ? savedKey : (keys[0] ?? null))
      })
      .catch((e: unknown) => current && setError(errorMessage(e)))
    return () => {
      current = false
    }
  }, [dataset, guidance])

  useEffect(() => {
    if (mode !== 'reviews' || !docId || !overview?.run) return
    let current = true
    setReview(null)
    backend
      .candidates({ dataset, guidance, doc_id: docId })
      .then((result: Review) => {
        if (!current) return
        setReview(result)
        const want = wanted.current
        const first = result.sections[0]?.section_id
        const id = want && result.sections.some((s) => s.section_id === want) ? want : first
        if (id) selectSection(id, result)
        else setSectionId(null)
      })
      .catch((e: unknown) => current && setError(errorMessage(e)))
    return () => {
      current = false
    }
  }, [dataset, guidance, docId, mode, overview])

  useEffect(() => {
    if (mode !== 'templates' || !templateKey || !overview?.run) return
    const [template, position] = templateKey.split('#')
    let current = true
    setMatches(null)
    backend
      .candidates({ dataset, guidance, template, template_position: Number(position) })
      .then((result: Matches) => current && setMatches(result))
      .catch((e: unknown) => current && setError(errorMessage(e)))
    return () => {
      current = false
    }
  }, [dataset, guidance, templateKey, mode, overview])

  // Every template section by its key, with its template.
  const templateSections = useMemo(() => {
    const map = new Map<string, { template: Template; section: TemplateSection }>()
    for (const template of overview?.templates ?? []) {
      for (const section of template.sections)
        map.set(keyOf(template.template, section.position), { template, section })
    }
    return map
  }, [overview])

  const reviews = useMemo(() => {
    const term = search.trim().toLowerCase()
    return (overview?.reviews ?? []).filter((r) => !term || r.doc_id.toLowerCase().includes(term))
  }, [overview, search])

  const byId = useMemo(
    () => new Map((review?.sections ?? []).map((s) => [s.section_id, s])),
    [review],
  )

  if (error)
    return <div className="message error">Couldn’t read the template candidates: {error}</div>

  const run = overview?.run ?? null
  const section = sectionId ? byId.get(sectionId) : undefined
  const chosen = candidateKey ? templateSections.get(candidateKey) : undefined
  const picked = templateKey ? templateSections.get(templateKey) : undefined
  const term = search.trim().toLowerCase()

  const controls = (
    <>
      <Toggle
        label="Dataset"
        options={DATASETS.map((d) => [d, d] as [string, string])}
        value={dataset}
        choose={(d) => d !== dataset && choose({ dataset: d })}
      />
      <Toggle
        label="Guidance"
        options={Object.entries(GUIDANCES).map(
          ([g, name]) => [g, name.replace(/ guidance$/, '')] as [string, string],
        )}
        value={guidance}
        choose={(g) => g !== guidance && choose({ guidance: g })}
      />
      <Toggle label="Show" options={MODES} value={mode} choose={chooseMode} />
    </>
  )

  const note = run && (
    <p className="note">
      <strong>Heading score</strong> (0 to 100): how alike the two headings’ words are, ignoring
      numbering such as “4.” or “Appendix 2”. A template section counts as a heading match only with{' '}
      {run.min_heading_score} or more. <strong>Text score</strong> (−1 to 1, in practice about 0.5
      to 0.9): how alike in meaning the section (its heading, the start of its text and its
      subheadings) is to what the template says goes there, from the {run.model} embedding model. A
      section’s <strong>best match</strong> by heading or by text is the template section with its
      highest score; equal scores all count. Each section’s candidates are its {run.k} best by text
      and its {run.k} best by heading (“found by”). The next step will have an LLM pick one of them,
      or none.
    </p>
  )

  return (
    <div className={mode === 'templates' ? 'two-columns' : 'three-columns'}>
      <aside className="list">
        {controls}
        {!overview ? (
          <p className="count">Loading…</p>
        ) : !run ? (
          <p className="message">
            No template candidates for {dataset} against the {GUIDANCES[guidance]} yet.
          </p>
        ) : (
          <>
            <p className="count">
              {formatNumber(run.n_sections)} sections · {formatNumber(run.n_candidates)} candidates.
              Found {formatDateTime(run.matched_at)} with {run.model.split('/').pop()}, by code{' '}
              {shortenId(run.git_commit)}.
            </p>
            {mode === 'reviews' ? (
              <>
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
                          {formatNumber(r.n_sections)} sections · {formatNumber(r.n_heading_match)}{' '}
                          with a heading match · {formatNumber(r.n_agree)} where heading and text
                          agree
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
                {reviews.length === 0 && (
                  <p className="muted small">No review matches “{search}”.</p>
                )}
              </>
            ) : (
              <>
                <input
                  type="search"
                  placeholder="Find a template section"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
                {(overview.templates ?? []).map((t) => {
                  const shown = t.sections.filter(
                    (s) =>
                      !term ||
                      s.heading.toLowerCase().includes(term) ||
                      t.title.toLowerCase().includes(term),
                  )
                  if (shown.length === 0) return null
                  return (
                    <div key={t.template} className="attr-group">
                      <h4>{t.title}</h4>
                      <ul>
                        {shown.map((s) => {
                          const key = keyOf(t.template, s.position)
                          return (
                            <li key={key}>
                              <button
                                className={key === templateKey ? 'review selected' : 'review'}
                                onClick={() => chooseTemplateSection(key)}
                              >
                                <span className="title">{s.heading}</span>
                                <span className="meta">
                                  best by text for {formatNumber(s.n_best_by_text)} · by heading for{' '}
                                  {formatNumber(s.n_best_by_heading)}
                                </span>
                              </button>
                            </li>
                          )
                        })}
                      </ul>
                    </div>
                  )
                })}
              </>
            )}
          </>
        )}
      </aside>

      {mode === 'reviews' && (
        <aside className="outline">
          {!run || !docId ? null : !review ? (
            <p className="count">Loading the review…</p>
          ) : (
            <>
              <h3 className="outline-title">{review.doc_id}</h3>
              <p className="count">
                Each section with its best match: ✓ where heading and text agree.
              </p>
              <ul>
                {review.sections.map((s) => (
                  <li key={s.section_id}>
                    <button
                      className={
                        s.section_id === sectionId ? 'outline-item selected' : 'outline-item'
                      }
                      style={{ paddingLeft: 8 + (s.level - 1) * 16 }}
                      onClick={() => selectSection(s.section_id, review)}
                    >
                      <span className="heading">{s.heading}</span>
                      <BestMatches section={s} />
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
        </aside>
      )}

      {mode === 'reviews' ? (
        <main className="details">
          {review && section ? (
            <>
              <header>
                {parents(section, byId).length > 0 && (
                  <p className="breadcrumb">
                    {parents(section, byId).map((p) => (
                      <React.Fragment key={p.section_id}>
                        <a onClick={() => selectSection(p.section_id, review)}>{p.heading}</a>{' '}
                        ›{' '}
                      </React.Fragment>
                    ))}
                  </p>
                )}
                <h2>{section.heading}</h2>
                <p className="doc-id">
                  {review.doc_id} · level {section.level} ·{' '}
                  {pages(section.first_page, section.last_page)}
                </p>
              </header>

              <section>
                <h3>Candidates from the {GUIDANCES[guidance]}</h3>
                <div
                  className="candidate-table"
                  role="region"
                  aria-label="Candidate scores table"
                  tabIndex={0}
                >
                  <table className="candidates">
                    <thead>
                      <tr>
                        <th>Template section</th>
                        <th>Template</th>
                        <th className="number">Heading</th>
                        <th className="number">Text</th>
                        <th>Found by</th>
                      </tr>
                    </thead>
                    <tbody>
                      {section.candidates.map((c) => {
                        const key = keyOf(c.template, c.template_position)
                        return (
                          <tr
                            key={key}
                            className={key === candidateKey ? 'selected' : ''}
                            onClick={() => setCandidateKey(key)}
                            title="Show what the template says goes here"
                          >
                            <td>{c.template_heading}</td>
                            <td className="muted">{templateName(c.template)}</td>
                            <td className={c.best_by_heading ? 'number best' : 'number'}>
                              {Math.round(c.heading_score)}{' '}
                              <span className="rank">#{c.heading_rank}</span>
                            </td>
                            <td className={c.best_by_text ? 'number best' : 'number'}>
                              {c.text_score.toFixed(2)} <span className="rank">#{c.text_rank}</span>
                            </td>
                            <td className="muted">{c.found_by}</td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
                <p className="muted small">
                  Bold: the section’s best match by that score. # is the rank among all{' '}
                  {formatNumber(templateSections.size)} template sections. Click a row to read that
                  template section.
                </p>
              </section>

              {chosen && (
                <section>
                  <h3>What the template says goes in “{chosen.section.heading}”</h3>
                  <TemplateText template={chosen.template} section={chosen.section} />
                </section>
              )}

              <section>
                <details
                  open={showText}
                  onToggle={(e) => {
                    const open = (e.target as HTMLDetailsElement).open
                    setShowText(open)
                    writeStoredValue('candidates:text', open)
                  }}
                >
                  <summary>The section’s own text</summary>
                  {showText && (
                    <SectionText dataset={dataset} docId={review.doc_id} section={section} />
                  )}
                </details>
              </section>
              {note}
            </>
          ) : review && review.sections.length === 0 ? (
            <div className="message">This review has no sections.</div>
          ) : null}
        </main>
      ) : (
        <main className="details">
          {run && picked && (
            <>
              <header>
                <p className="breadcrumb">{picked.template.title}</p>
                <h2>{picked.section.heading}</h2>
              </header>
              <TemplateText template={picked.template} section={picked.section} />
              <section>
                <h3>Review sections that match it best</h3>
                <p className="muted">
                  In {dataset}, it is the best match by text for{' '}
                  {formatNumber(picked.section.n_best_by_text)} sections and by heading for{' '}
                  {formatNumber(picked.section.n_best_by_heading)}; both for{' '}
                  {formatNumber(picked.section.n_best_by_both)}.
                </p>
                {!matches ? (
                  <p className="muted">Loading…</p>
                ) : matches.matches.length === 0 ? (
                  <p className="muted">No section of any review has it as its best match.</p>
                ) : (
                  <>
                    {matches.n_matches > matches.matches.length && (
                      <p className="muted small">
                        Showing the first {formatNumber(matches.matches.length)} of{' '}
                        {formatNumber(matches.n_matches)}: both first, then by heading score and
                        text score.
                      </p>
                    )}
                    <div
                      className="candidate-table"
                      role="region"
                      aria-label="Matching review sections table"
                      tabIndex={0}
                    >
                      <table className="candidates">
                        <thead>
                          <tr>
                            <th>Review</th>
                            <th>Section</th>
                            <th className="number">Heading</th>
                            <th className="number">Text</th>
                            <th>Best by</th>
                          </tr>
                        </thead>
                        <tbody>
                          {matches.matches.map((m) => (
                            <tr
                              key={`${m.doc_id}#${m.section_id}`}
                              onClick={() => openMatch(m)}
                              title="Open this section with all its candidates"
                            >
                              <td className="muted">{m.doc_id}</td>
                              <td>{m.heading}</td>
                              <td className={m.best_by_heading ? 'number best' : 'number'}>
                                {Math.round(m.heading_score)}
                              </td>
                              <td className={m.best_by_text ? 'number best' : 'number'}>
                                {m.text_score.toFixed(2)}
                              </td>
                              <td className="muted">{bestBy(m)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </>
                )}
              </section>
              {note}
            </>
          )}
        </main>
      )}
    </div>
  )
}
