import React, { useEffect, useMemo, useRef, useState } from 'react'
import { backend } from './wmill'
import {
  DATASETS,
  GUIDANCES,
  errorMessage,
  formatDateTime,
  formatNumber,
  formatPercent,
  readStoredValue,
  shortenId,
  writeStoredValue,
} from './shared'
import { Section, SectionText, pages, parents } from './SectionText'

// What f/dhr/O1/choose_template_section/get_choices returns.
type Version = {
  revision: string
  reviews: 'pilot' | 'all'
  model: string
  model_revision: string | null
  prompt_version: string
  chosen_at: string
  git_commit: string | null
  slurm_job_ids: string[] | null
  blythe_runs: string[] | null
  n_reviews: number
  n_reviews_matched: number
  n_sections: number
  n_matched: number
  n_unmatched: number
  n_no_answer: number
  n_reviews_doubtful: number
  n_doubtful: number
  run: {
    vllm: string | null
    gpu: string | null
    driver: string | null
    prompt_tokens_per_second: number | null
    seconds_generating: number | null
  }
  guidance_title: string
  guidance_url: string | null
}
type TemplateSection = {
  position: number
  heading: string
  printed: boolean
  guidance: string
  page: number | null
  n_chosen: number
  n_doubtful: number
}
type Template = {
  template: string
  appendix: number
  title: string
  kind: string
  n_reviews: number // reviews written to it
  sections: TemplateSection[]
}
type Status = 'matched' | 'unmatched' | 'no answer'
type ReviewRow = {
  doc_id: string
  status: Status // of its template
  template: string | null
  template_title: string | null
  probability: number | null
  error: string | null
  fallback: boolean // the model said "none of these" but wasn't sure: given its likeliest template
  doubtful: boolean // its template is doubtful: check by hand
  n_sections: number
  n_matched: number
  n_unmatched: number
  n_no_answer: number
  n_doubtful: number
}
type Overview = {
  dataset: string
  guidance: string
  version: Version | null
  doubtful_below?: number
  n_doubtful?: number
  templates?: Template[]
  reviews?: ReviewRow[]
}
type WeighedTemplate = {
  template: string | null // null: none of these
  title: string
  kind: string | null
  probability: number
  chosen: boolean
}
type DocumentChoice = {
  status: Status
  template: string | null
  template_title: string | null
  template_kind: string | null
  answer: string | null
  probability: number | null
  error: string | null
  fallback: boolean
  doubtful: boolean
  weighed: WeighedTemplate[]
}
type Weighed = {
  template: string | null // null: none of these
  template_position: number | null
  template_title: string | null
  template_heading: string
  probability: number
  chosen: boolean
  heading_rank: number | null // step 4's ranks, if it was one of step 4's candidates
  text_rank: number | null
}
type ChoiceSection = Section & {
  status: Status | null // null: not asked about (outside a pilot run)
  template: string | null
  template_position: number | null
  template_title: string | null
  template_heading: string | null
  answer: string | null
  probability: number | null
  none_probability: number | null
  n_options: number | null
  error: string | null
  doubtful: boolean // under 50%, or its review's template is doubtful: check by hand
  weighed: Weighed[]
}
// One part of the review in its template's order (corpus.current_standardised).
type Part = {
  part: number
  status: 'template section' | 'unmatched' | 'no answer'
  template: string | null
  template_title: string | null
  template_position: number | null
  template_heading: string | null
  n_sections: number
  section_ids: string[]
  min_probability: number | null
}
type Review = {
  doc_id: string
  document: DocumentChoice | null // null: not asked about (outside a pilot run)
  sections: ChoiceSection[]
  standardised: Part[]
}
type Found = {
  doc_id: string
  section_id: string
  level: number
  heading: string
  position: number
  probability: number | null
  none_probability: number | null
  error: string | null
  doubtful: boolean
  template_heading: string | null
}
type FoundList = { n_found: number; found: Found[] }

type Mode = 'reviews' | 'templates'
const MODES: [Mode, string][] = [
  ['reviews', 'Reviews'],
  ['templates', 'Template sections'],
]
type Order = 'standardised' | 'written'
const ORDERS: [Order, string][] = [
  ['standardised', 'Template order'],
  ['written', 'As written'],
]
// The template-section list also offers the flagged sections.
const FLAGGED: [string, string][] = [
  ['doubtful', 'Doubtful: check by hand'],
  ['unmatched', 'Unmatched'],
  ['no answer', 'No answer from the model'],
]
const DOUBTFUL = 0.5

const keyOf = (template: string, position: number) => `${template}#${position}`

function percent(p: number | null | undefined): string {
  return p === null || p === undefined ? '—' : `${Math.round(p * 100)}%`
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

// How sure the model was: a bar from 0 to 100%.
function Confidence({ p }: { p: number | null | undefined }) {
  if (p === null || p === undefined) return <span className="muted">—</span>
  return (
    <span className={p < DOUBTFUL ? 'confidence doubtful' : 'confidence'}>
      <span className="confidence-bar" aria-hidden="true">
        <span style={{ width: `${Math.round(p * 100)}%` }} />
      </span>
      {percent(p)}
    </span>
  )
}

// The mark every doubtful answer carries, so none is missed.
function DoubtBadge({ title }: { title?: string }) {
  return (
    <span className="doubt-badge" title={title ?? 'Doubtful: check this answer by hand'}>
      ⚠ Doubtful
    </span>
  )
}

// Why an answer is doubtful, in a sentence.
function doubtReason(section: ChoiceSection, document: DocumentChoice | null): string {
  const own = section.probability !== null && section.probability < DOUBTFUL
  const review = document?.doubtful
  if (own && review)
    return `The model was only ${percent(section.probability)} sure, and this review’s template is itself doubtful.`
  if (own) return `The model was only ${percent(section.probability)} sure of this answer.`
  return 'This review’s template is doubtful, so every section mapped onto it needs checking.'
}

// The probability the model gave a review's template (a fallback's own, not "none of these").
function templateProbability(document: DocumentChoice | ReviewRow): number | null {
  if (!document.fallback) return document.probability
  const weighed = 'weighed' in document ? document.weighed : []
  return weighed.find((w) => w.template === document.template)?.probability ?? null
}

// A section's choice in a line, for the outline.
function ChoiceLine({ section, short }: { section: ChoiceSection; short?: boolean }) {
  if (section.status === 'matched')
    return (
      <span className={section.doubtful ? 'match doubtful' : 'match agree'}>
        {section.doubtful && <DoubtBadge />}
        {short ? '' : `→ ${section.template_heading} (${section.template_title}) · `}
        {percent(section.probability)}
      </span>
    )
  if (section.status === 'unmatched')
    return (
      <span className="match flagged">
        {section.answer
          ? `Unmatched: none of these · ${percent(section.probability)}`
          : 'Unmatched: its review matched no template'}
      </span>
    )
  if (section.status === 'no answer') return <span className="match flagged">No answer</span>
  return <span className="match">Not asked</span>
}

// The review's template, for the top of the outline.
function DocumentLine({ document }: { document: DocumentChoice | null }) {
  if (!document) return <p className="muted small">Not asked about: outside this pilot run.</p>
  if (document.status === 'matched' && document.fallback)
    return (
      <div className="choice-card compact doubtful">
        <p>
          <DoubtBadge /> Mapped onto the <strong>{document.template_title}</strong> as a fallback
        </p>
        <p>
          The model said this review follows none of the templates, but was only{' '}
          {percent(document.probability)} sure, so it was given its likeliest template (
          {percent(templateProbability(document))}). Often a review is only part of a report. Check
          it, and every section below, by hand.
        </p>
      </div>
    )
  if (document.status === 'matched')
    return (
      <div className={document.doubtful ? 'choice-card compact doubtful' : 'choice-card compact'}>
        <p>
          {document.doubtful && <DoubtBadge />} Written to the{' '}
          <strong>{document.template_title}</strong>
        </p>
        <p>
          Confidence <Confidence p={document.probability} />
          {document.doubtful && ' · check by hand, with every section below'}
        </p>
      </div>
    )
  return (
    <div className="choice-card compact flagged">
      <p>
        <strong>
          {document.status === 'unmatched' ? 'Matches none of the templates' : 'No answer'}
        </strong>
      </p>
      <p className="muted">
        {document.status === 'unmatched'
          ? `The model chose “none of these” (${percent(document.probability)}), so its sections weren’t mapped; they are kept and flagged as unmatched.`
          : document.error}
      </p>
    </div>
  )
}

export default function ChoicesTab() {
  const [dataset, setDataset] = useState(() => {
    const saved = readStoredValue<string>('choices:dataset')
    return saved && DATASETS.includes(saved) ? saved : DATASETS[0]
  })
  const [guidance, setGuidance] = useState(() => {
    const saved = readStoredValue<string>('choices:guidance')
    return saved && saved in GUIDANCES ? saved : Object.keys(GUIDANCES)[0]
  })
  const [mode, setMode] = useState<Mode>(() => readStoredValue<Mode>('choices:mode') ?? 'reviews')
  const [order, setOrder] = useState<Order>(
    () => readStoredValue<Order>('choices:order') ?? 'standardised',
  )
  const [overview, setOverview] = useState<Overview | null>(null)
  const [docId, setDocId] = useState<string | null>(null)
  const [review, setReview] = useState<Review | null>(null)
  const [sectionId, setSectionId] = useState<string | null>(null)
  const [partNo, setPartNo] = useState<number | null>(null) // a template section of the review
  const [listKey, setListKey] = useState<string | null>(null) // a template section or a flag
  const [found, setFound] = useState<FoundList | null>(null)
  const [showText, setShowText] = useState(() => readStoredValue<boolean>('choices:text') ?? false)
  const [search, setSearch] = useState('')
  const [error, setError] = useState<string | null>(null)
  const wanted = useRef<string | null>(null) // the section to open once a review has loaded

  function choose(next: { dataset?: string; guidance?: string }) {
    if (next.dataset) {
      setDataset(next.dataset)
      writeStoredValue('choices:dataset', next.dataset)
      wanted.current = null
    }
    if (next.guidance) {
      setGuidance(next.guidance)
      writeStoredValue('choices:guidance', next.guidance)
    }
    setOverview(null)
    setReview(null)
    setFound(null)
  }

  function chooseMode(next: Mode) {
    if (next !== mode) setSearch('')
    setMode(next)
    writeStoredValue('choices:mode', next)
  }

  function chooseOrder(next: Order) {
    setOrder(next)
    writeStoredValue('choices:order', next)
  }

  function chooseReview(id: string) {
    if (id !== docId) wanted.current = null
    setDocId(id)
    writeStoredValue(`choices:doc:${dataset}`, id)
  }

  function chooseSection(id: string) {
    setSectionId(id)
    setPartNo(null)
    wanted.current = id
  }

  function chooseList(key: string) {
    setListKey(key)
    writeStoredValue('choices:list', key)
  }

  function openFound(f: Found) {
    chooseReview(f.doc_id)
    wanted.current = f.section_id
    setPartNo(null)
    chooseMode('reviews')
  }

  useEffect(() => {
    let current = true
    backend
      .choices({ dataset, guidance })
      .then((result: Overview) => {
        if (!current) return
        setOverview(result)
        const ids = (result.reviews ?? []).map((r) => r.doc_id)
        setDocId((id) => {
          const saved = readStoredValue<string>(`choices:doc:${dataset}`)
          return id && ids.includes(id)
            ? id
            : saved && ids.includes(saved)
              ? saved
              : (ids[0] ?? null)
        })
        const keys = [
          ...(result.templates ?? []).flatMap((t) =>
            t.sections.map((s) => keyOf(t.template, s.position)),
          ),
          ...FLAGGED.map(([k]) => k),
        ]
        const saved = readStoredValue<string>('choices:list')
        setListKey(saved && keys.includes(saved) ? saved : (keys[0] ?? null))
      })
      .catch((e: unknown) => current && setError(errorMessage(e)))
    return () => {
      current = false
    }
  }, [dataset, guidance])

  useEffect(() => {
    if (mode !== 'reviews' || !docId || !overview?.version) return
    let current = true
    setReview(null)
    backend
      .choices({ dataset, guidance, doc_id: docId })
      .then((result: Review) => {
        if (!current) return
        setReview(result)
        const want = wanted.current
        const asked = result.sections.find((s) => s.status !== null)?.section_id
        const id =
          want && result.sections.some((s) => s.section_id === want)
            ? want
            : (asked ?? result.sections[0]?.section_id ?? null)
        setSectionId(id)
        wanted.current = id
      })
      .catch((e: unknown) => current && setError(errorMessage(e)))
    return () => {
      current = false
    }
  }, [dataset, guidance, docId, mode, overview])

  useEffect(() => {
    if (mode !== 'templates' || !listKey || !overview?.version) return
    let current = true
    setFound(null)
    const flag = FLAGGED.some(([k]) => k === listKey)
    const [template, position] = listKey.split('#')
    backend
      .choices(
        flag
          ? { dataset, guidance, status: listKey }
          : { dataset, guidance, template, template_position: Number(position) },
      )
      .then((result: FoundList) => current && setFound(result))
      .catch((e: unknown) => current && setError(errorMessage(e)))
    return () => {
      current = false
    }
  }, [dataset, guidance, listKey, mode, overview])

  const templateSections = useMemo(() => {
    const map = new Map<string, { template: Template; section: TemplateSection }>()
    for (const template of overview?.templates ?? [])
      for (const section of template.sections)
        map.set(keyOf(template.template, section.position), { template, section })
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

  if (error) return <div className="message error">Couldn’t read the template choices: {error}</div>

  const version = overview?.version ?? null
  const section = sectionId ? byId.get(sectionId) : undefined
  const part = partNo !== null ? review?.standardised.find((p) => p.part === partNo) : undefined
  const chosen =
    section?.template && section.template_position !== null
      ? templateSections.get(keyOf(section.template, section.template_position))
      : undefined
  const term = search.trim().toLowerCase()
  const flaggedCount = (key: string) =>
    key === 'doubtful'
      ? (version?.n_doubtful ?? 0)
      : key === 'unmatched'
        ? (version?.n_unmatched ?? 0)
        : (version?.n_no_answer ?? 0)
  const picked = listKey ? templateSections.get(listKey) : undefined
  const pickedFlag = FLAGGED.find(([k]) => k === listKey)

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

  const note = version && (
    <p className="note">
      Two questions were put to {version.model.split('/').pop()} on Blythe, each answered with one
      letter. First, which of the guidance’s templates each whole review is written to, from its
      title, opening text and headings. Then, for each section of a review that has a template,
      which section of that template (or of the action plan that goes with it) it is, or none of
      them, lettered in the guidance’s own order, from the section’s heading, where it sits, its
      subsections and the start of its text. Several sections can share one template section; their
      text is never changed. <strong>Confidence</strong> is the probability the model gave its
      letter (from its first-token probabilities). An answer is marked <strong>⚠ Doubtful</strong>{' '}
      when it is under {percent(DOUBTFUL)}, or when its review’s template is: a review the model
      said follows none of the templates, but with under 80% confidence, is given its likeliest
      template as a fallback, and it and all its sections are doubtful. Prompt{' '}
      {version.prompt_version}.
    </p>
  )

  // The review's sections in its template's order, each template section with its sections.
  const standardisedOutline = review && (
    <ul>
      {review.standardised.map((p) => {
        const previous = review.standardised.find((q) => q.part === p.part - 1)
        const newTemplate = p.status === 'template section' && previous?.template !== p.template
        return (
          <li key={p.part}>
            {newTemplate && <p className="part-template">{p.template_title}</p>}
            <button
              className={
                (partNo === p.part ? 'outline-item part selected' : 'outline-item part') +
                (p.n_sections === 0 ? ' empty' : '') +
                (p.status !== 'template section' ? ' flagged' : '')
              }
              onClick={() => {
                setPartNo(p.part)
                setSectionId(null)
              }}
            >
              <span className="heading">
                {p.status === 'template section'
                  ? p.template_heading
                  : p.status === 'unmatched'
                    ? 'Unmatched'
                    : 'No answer'}
              </span>
              <span className="meta">
                {p.n_sections === 0
                  ? 'Not in this review'
                  : `${formatNumber(p.n_sections)} section${p.n_sections === 1 ? '' : 's'}`}
                {p.status === 'template section' &&
                  p.section_ids.some((id) => byId.get(id)?.doubtful) && (
                    <span className="doubt-inline"> · ⚠ doubtful</span>
                  )}
              </span>
            </button>
            {p.section_ids.length > 0 && (
              <ul className="part-sections">
                {p.section_ids.map((id) => {
                  const s = byId.get(id)
                  if (!s) return null
                  return (
                    <li key={id}>
                      <button
                        className={id === sectionId ? 'outline-item selected' : 'outline-item'}
                        onClick={() => chooseSection(id)}
                      >
                        <span className="heading">{s.heading}</span>
                        <ChoiceLine section={s} short={p.status === 'template section'} />
                      </button>
                    </li>
                  )
                })}
              </ul>
            )}
          </li>
        )
      })}
    </ul>
  )

  const writtenOutline = review && (
    <ul>
      {review.sections.map((s) => (
        <li key={s.section_id}>
          <button
            className={s.section_id === sectionId ? 'outline-item selected' : 'outline-item'}
            style={{ paddingLeft: 8 + (s.level - 1) * 16 }}
            onClick={() => chooseSection(s.section_id)}
          >
            <span className="heading">{s.heading}</span>
            <ChoiceLine section={s} />
          </button>
        </li>
      ))}
    </ul>
  )

  // One template section of the review: its guidance, then each of the review's sections
  // mapped to it with its own text, unchanged.
  const partDetails = review && part && (
    <>
      <header>
        <p className="breadcrumb">
          {review.doc_id} › {part.status === 'template section' ? part.template_title : 'Flagged'}
        </p>
        <h2>
          {part.status === 'template section'
            ? part.template_heading
            : part.status === 'unmatched'
              ? 'Unmatched sections'
              : 'Sections without an answer'}
        </h2>
      </header>
      {part.status === 'template section' && part.template && part.template_position !== null && (
        <section>
          {templateSections.get(keyOf(part.template, part.template_position))?.section.guidance ? (
            <p className="guidance-text">
              {templateSections.get(keyOf(part.template, part.template_position))!.section.guidance}
            </p>
          ) : (
            <p className="muted">The template gives only the heading here.</p>
          )}
        </section>
      )}
      <section>
        <h3>
          {part.n_sections === 0
            ? 'This review has no section for it'
            : `The review’s ${part.n_sections === 1 ? 'section' : `${formatNumber(part.n_sections)} sections`} here, in the review’s order`}
        </h3>
        {part.n_sections > 1 && part.status === 'template section' && (
          <p className="muted small">
            Shown together because each was mapped to this template section. Each keeps its own
            heading, and its text is exactly as in the review: nothing is merged or reworded.
            Subsections mapped elsewhere appear under their own template section.
          </p>
        )}
        {part.section_ids.map((id) => {
          const s = byId.get(id)
          if (!s) return null
          const above = parents(s, byId)
          return (
            <div key={id} className="part-section">
              <h4>
                <a onClick={() => chooseSection(id)} title="Open this section’s choice">
                  {s.heading}
                </a>
              </h4>
              <p className="part-meta">
                {above.length > 0 && `In ${above.map((a) => a.heading).join(' › ')} · `}
                {pages(s.first_page, s.last_page)}
                {s.status === 'matched' && (
                  <>
                    {' · '}
                    <Confidence p={s.probability} />
                  </>
                )}
                {s.doubtful && (
                  <>
                    {' '}
                    <DoubtBadge title={doubtReason(s, review.document)} />
                  </>
                )}
              </p>
              <SectionText dataset={dataset} docId={review.doc_id} section={s} ownOnly />
            </div>
          )
        })}
      </section>
      {note}
    </>
  )

  return (
    <div className={mode === 'templates' ? 'two-columns' : 'three-columns'}>
      <aside className="list">
        {controls}
        {!overview ? (
          <p className="count">Loading…</p>
        ) : !version ? (
          <p className="message">
            No template choices for {dataset} against the {GUIDANCES[guidance]} yet. They are made
            on Blythe by step 5.
          </p>
        ) : (
          <>
            <p className="count">
              {version.reviews === 'pilot' ? 'Pilot reviews only · ' : ''}
              {formatNumber(version.n_reviews_matched)} of {formatNumber(version.n_reviews)} reviews
              given a template. {formatNumber(version.n_sections)} sections:{' '}
              {formatNumber(version.n_matched)} matched (
              {formatPercent(version.n_matched, version.n_sections)}),{' '}
              {formatNumber(version.n_unmatched)} unmatched
              {version.n_no_answer
                ? `, ${formatNumber(version.n_no_answer)} without an answer`
                : ''}
              . Chosen {formatDateTime(version.chosen_at)} by code {shortenId(version.git_commit)}.
            </p>
            {(version.n_doubtful > 0 || version.n_reviews_doubtful > 0) && (
              <button
                className="doubt-banner"
                onClick={() => {
                  chooseMode('templates')
                  chooseList('doubtful')
                }}
              >
                <strong>
                  ⚠ {formatNumber(version.n_doubtful)} doubtful section
                  {version.n_doubtful === 1 ? '' : 's'}
                  {version.n_reviews_doubtful
                    ? ` and ${formatNumber(version.n_reviews_doubtful)} doubtful review${version.n_reviews_doubtful === 1 ? '' : 's'}`
                    : ''}
                </strong>
                <span>Check these by hand before relying on them. Show the list →</span>
              </button>
            )}
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
                        className={
                          (r.doc_id === docId ? 'review selected' : 'review') +
                          (r.doubtful ? ' doubtful' : '')
                        }
                        onClick={() => chooseReview(r.doc_id)}
                      >
                        <span className="title">
                          {r.doubtful && <DoubtBadge title="Its template is doubtful: check it" />}
                          {r.doc_id}
                        </span>
                        <span
                          className={
                            r.status === 'matched'
                              ? r.doubtful
                                ? 'meta doubtful'
                                : 'meta'
                              : 'meta flagged'
                          }
                        >
                          {r.status === 'matched'
                            ? r.fallback
                              ? `Fallback: ${r.template_title} (model said none of them, ${percent(r.probability)})`
                              : `${r.template_title} · ${percent(r.probability)}`
                            : r.status === 'unmatched'
                              ? 'No template: matches none of them'
                              : 'No answer for its template'}
                        </span>
                        <span className="meta">
                          {formatNumber(r.n_matched)} of {formatNumber(r.n_sections)} sections
                          matched · {formatNumber(r.n_unmatched)} unmatched
                          {r.n_no_answer ? ` · ${formatNumber(r.n_no_answer)} no answer` : ''}
                        </span>
                        {r.n_doubtful > 0 && (
                          <span className="meta doubtful">
                            ⚠ {formatNumber(r.n_doubtful)} doubtful section
                            {r.n_doubtful === 1 ? '' : 's'}
                          </span>
                        )}
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
                <div className="attr-group">
                  <h4>Flagged</h4>
                  <ul>
                    {FLAGGED.map(([key, label]) => (
                      <li key={key}>
                        <button
                          className={key === listKey ? 'review selected' : 'review'}
                          onClick={() => chooseList(key)}
                        >
                          <span className="title">{label}</span>
                          <span className="meta">{formatNumber(flaggedCount(key))} sections</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
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
                      <h4>
                        {t.title}{' '}
                        <span className="muted small">
                          {t.kind === 'action plan'
                            ? '· goes with a report'
                            : `· ${formatNumber(t.n_reviews)} review${t.n_reviews === 1 ? '' : 's'}`}
                        </span>
                      </h4>
                      <ul>
                        {shown.map((s) => {
                          const key = keyOf(t.template, s.position)
                          return (
                            <li key={key}>
                              <button
                                className={key === listKey ? 'review selected' : 'review'}
                                onClick={() => chooseList(key)}
                              >
                                <span className="title">{s.heading}</span>
                                <span className="meta">
                                  {formatNumber(s.n_chosen)} review sections
                                  {s.n_doubtful ? ` · ${formatNumber(s.n_doubtful)} doubtful` : ''}
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
          {!version || !docId ? null : !review ? (
            <p className="count">Loading the review…</p>
          ) : (
            <>
              <h3 className="outline-title">{review.doc_id}</h3>
              <DocumentLine document={review.document} />
              <Toggle label="Order" options={ORDERS} value={order} choose={chooseOrder} />
              <p className="count">
                {order === 'standardised'
                  ? 'Its template’s sections in the guidance’s order, each with the review’s sections mapped to it.'
                  : 'Each section with the template section it was mapped to.'}
              </p>
              {order === 'standardised' ? standardisedOutline : writtenOutline}
            </>
          )}
        </aside>
      )}

      {mode === 'reviews' ? (
        <main className="details">
          {partDetails ? (
            partDetails
          ) : review && section ? (
            <>
              <header>
                {parents(section, byId).length > 0 && (
                  <p className="breadcrumb">
                    {parents(section, byId).map((p) => (
                      <React.Fragment key={p.section_id}>
                        <a onClick={() => chooseSection(p.section_id)}>{p.heading}</a> ›{' '}
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
                <h3>Mapped to, in the {GUIDANCES[guidance]}</h3>
                {section.status === 'matched' && chosen ? (
                  <div className={section.doubtful ? 'choice-card doubtful' : 'choice-card'}>
                    {section.doubtful && (
                      <p className="doubt-line">
                        <DoubtBadge /> Check this by hand. {doubtReason(section, review.document)}
                      </p>
                    )}
                    <p className="choice-name">
                      <strong>{chosen.section.heading}</strong>{' '}
                      <span className="muted">in the {chosen.template.title}</span>
                    </p>
                    <p>
                      Confidence <Confidence p={section.probability} />
                    </p>
                    {chosen.section.guidance ? (
                      <p className="guidance-text">{chosen.section.guidance}</p>
                    ) : (
                      <p className="muted">The template gives only the heading here.</p>
                    )}
                  </div>
                ) : section.status === 'unmatched' ? (
                  <div className="choice-card flagged">
                    <p className="choice-name">
                      <strong>Unmatched</strong>{' '}
                      <span className="muted">
                        {section.answer
                          ? `the model chose “none of these” (${percent(section.probability)})`
                          : 'its review matches none of the templates'}
                      </span>
                    </p>
                    <p className="muted">
                      The section is kept in the database and flagged, so material the templates
                      don’t cover stays findable.
                    </p>
                  </div>
                ) : section.status === 'no answer' ? (
                  <div className="choice-card flagged">
                    <p className="choice-name">
                      <strong>No answer</strong>
                    </p>
                    <p className="muted">{section.error}</p>
                  </div>
                ) : (
                  <p className="muted">
                    Not asked about: this run covered only the pilot reviews’ sections.
                  </p>
                )}
              </section>

              {section.weighed.length > 0 && (
                <section>
                  <h3>What the model weighed</h3>
                  <div
                    className="candidate-table"
                    role="region"
                    aria-label="The options the model gave any weight to, and its confidence in each"
                    tabIndex={0}
                  >
                    <table className="candidates with-confidence">
                      <thead>
                        <tr>
                          <th>Template section</th>
                          <th>Template</th>
                          <th className="number">Model</th>
                          <th className="number">Step 4 rank</th>
                        </tr>
                      </thead>
                      <tbody>
                        {section.weighed.map((w) => (
                          <tr
                            key={w.template ? keyOf(w.template, w.template_position!) : 'none'}
                            className={w.chosen ? 'selected' : ''}
                          >
                            <td>
                              {w.chosen ? '→ ' : ''}
                              {w.template_heading}
                            </td>
                            <td className="muted">{w.template_title}</td>
                            <td className="number">
                              <Confidence p={w.probability} />
                            </td>
                            <td className="number muted">
                              {w.text_rank !== null
                                ? `#${w.text_rank} text, #${w.heading_rank} heading`
                                : '—'}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <p className="muted small">
                    Of the {formatNumber(section.n_options ?? 0)} template sections it chose from,
                    those the model gave at least 1%, likeliest first. Step 4 rank: where step 4’s
                    text and heading scores placed it among the section’s candidates (— if it wasn’t
                    one).
                  </p>
                </section>
              )}

              {review.document && review.document.weighed.length > 0 && (
                <section>
                  <h3>The review’s template: what the model weighed</h3>
                  <div
                    className="candidate-table"
                    role="region"
                    aria-label="Templates"
                    tabIndex={0}
                  >
                    <table className="candidates with-confidence">
                      <thead>
                        <tr>
                          <th>Template</th>
                          <th>Kind</th>
                          <th className="number">Model</th>
                          <th />
                        </tr>
                      </thead>
                      <tbody>
                        {review.document.weighed.map((w) => (
                          <tr key={w.template ?? 'none'} className={w.chosen ? 'selected' : ''}>
                            <td>
                              {w.chosen ? '→ ' : ''}
                              {w.title}
                            </td>
                            <td className="muted">{w.kind}</td>
                            <td className="number">
                              <Confidence p={w.probability} />
                            </td>
                            <td />
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </section>
              )}

              <section>
                <details
                  open={showText}
                  onToggle={(e) => {
                    const open = (e.target as HTMLDetailsElement).open
                    setShowText(open)
                    writeStoredValue('choices:text', open)
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
          {version && (picked || pickedFlag) && (
            <>
              <header>
                <p className="breadcrumb">{picked ? picked.template.title : 'Flagged'}</p>
                <h2>{picked ? picked.section.heading : pickedFlag![1]}</h2>
              </header>
              {picked ? (
                picked.section.guidance ? (
                  <p className="guidance-text">{picked.section.guidance}</p>
                ) : (
                  <p className="muted">The template gives only the heading here.</p>
                )
              ) : (
                <p className={listKey === 'doubtful' ? 'doubt-line' : 'muted'}>
                  {listKey === 'doubtful'
                    ? `Answers to check by hand: the model was under ${percent(DOUBTFUL)} sure, or the section’s review was only given its template as a fallback or with under ${percent(DOUBTFUL)} confidence.`
                    : listKey === 'unmatched'
                      ? 'Sections the model said are none of their template’s sections, and every section of a review that matches none of the templates.'
                      : 'Sections the model gave no usable answer for (see the reason beside each).'}
                </p>
              )}
              <section>
                <h3>{picked ? 'Review sections mapped to it' : 'The sections'}</h3>
                {!found ? (
                  <p className="muted">Loading…</p>
                ) : found.found.length === 0 ? (
                  <p className="muted">None in {dataset}.</p>
                ) : (
                  <>
                    <p className="muted small">
                      {formatNumber(found.n_found)} in {dataset}
                      {found.n_found > found.found.length
                        ? `; showing the first ${formatNumber(found.found.length)}`
                        : ''}
                      , {picked ? 'surest first' : 'least sure first'}. Click one to open it.
                    </p>
                    <div
                      className="candidate-table"
                      role="region"
                      aria-label="Review sections table"
                      tabIndex={0}
                    >
                      <table className="candidates">
                        <thead>
                          <tr>
                            <th>Review</th>
                            <th>Section</th>
                            {listKey === 'doubtful' && <th>Mapped to</th>}
                            <th className="number">Confidence</th>
                          </tr>
                        </thead>
                        <tbody>
                          {found.found.map((f) => (
                            <tr
                              key={`${f.doc_id}#${f.section_id}`}
                              onClick={() => openFound(f)}
                              title="Open this section"
                            >
                              <td className="muted">{f.doc_id}</td>
                              <td>
                                {f.doubtful && listKey !== 'doubtful' && <DoubtBadge />}
                                {f.heading}
                              </td>
                              {listKey === 'doubtful' && (
                                <td className="muted">{f.template_heading}</td>
                              )}
                              <td className="number">
                                {f.error ? (
                                  <span className="muted">{f.error}</span>
                                ) : (
                                  <Confidence p={f.probability} />
                                )}
                              </td>
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
