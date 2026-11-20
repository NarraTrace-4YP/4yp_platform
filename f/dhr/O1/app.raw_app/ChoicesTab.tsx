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
  n_parent: number // sections given their parent section's template section
  n_extra: number // sections given an extra section
  n_nearest: number // sections given the model's likeliest after "none of these"
  refined_at: string | null
  n_combined: number // torn between two template sections that together it was sure of
  n_subsections: number // headings that took their subsections' mapping
  n_appendix: number // in an appendix
  n_other: number // the catch-all: nothing else fits
  n_reviews_by_sections: number // reviews given the template their sections fit best
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
  method: 'model' | 'sections' // 'sections': the template its sections fit best
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
  method: 'model' | 'sections'
  sections_fit: number | null // share of its text that fits its template
  sections_margin: number | null // how far ahead of the next template
  sections_scores: Record<string, number>
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
  doubtful: boolean // its own mapping is doubtful: check by hand
  review_doubtful: boolean // its review's template is doubtful
  also_template: string | null // combined: the other template section; appendix: what it's about
  also_position: number | null
  also_heading: string | null
  model_template: string | null // the model's own choice, whatever the rules made of it
  model_template_position: number | null
  method: Method | null
  mapped_probability: number | null // how sure the mapping is, whichever way it was made
  extra_answer: string | null // the extra-sections question, if it was asked
  extra_probability: number | null
  extra_probabilities: Record<string, number>
  weighed: Weighed[]
}
// How a section got its template section (refine.py).
type Method =
  | 'model'
  | 'combined'
  | 'extra'
  | 'parent'
  | 'nearest'
  | 'subsections'
  | 'other'
  | 'appendix'
const METHOD_TAG: Record<Method, string> = {
  model: '',
  combined: '⇄ two sections in one · ',
  extra: '+ extra section · ',
  parent: '↳ with its parent · ',
  nearest: '≈ nearest fit · ',
  subsections: '↥ from its subsections · ',
  other: '… other content · ',
  appendix: '§ appendix · ',
}
// What each way of mapping means, for a section's card.
function methodText(s: ChoiceSection): string | null {
  switch (s.method) {
    case 'combined':
      return `The model was torn between this and ${s.also_heading ?? 'another template section'}, together ${percent(s.mapped_probability)} sure: the section is both, so it is filed here and also counts as that.`
    case 'extra':
      return s.answer === 'Z'
        ? 'The model said this section fits none of the template’s sections, then chose this extra section when asked.'
        : `The model matched this only ${percent(s.probability)} sure, then was surer (${percent(s.mapped_probability)}) that it is this extra section.`
    case 'parent':
      return 'The model said this section fits none of the template’s sections; it goes with the section it sits in.'
    case 'nearest':
      return `The model said this section fits none of the template’s sections, but only ${percent(s.none_probability)} sure; this was its likeliest.`
    case 'subsections':
      return s.template_heading === 'Report part heading'
        ? 'A heading with little text of its own whose subsections are about different things: it only names a part of the report.'
        : `A heading with little text of its own: it takes what most of its subsections’ text (${percent(s.mapped_probability)}) was mapped to.`
    case 'other':
      return 'Nothing fits it: not the template, not its parent, not any extra section. Kept together with the rest of its kind for checking.'
    case 'appendix':
      return `It is in the report’s appendices${s.also_heading ? `; it is about ${s.also_heading}` : ''}.`
    default:
      return null
  }
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
  n_opening_blocks: number // text before the review's first heading (its title page) shown here
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
  method: Method | null
  also_heading: string | null
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

// Why an answer is doubtful, in a sentence. Only its own mapping counts: a doubtful review
// template is shown on the review.
function doubtReason(section: ChoiceSection): string {
  switch (section.method) {
    case 'nearest':
      return `The model said this fits none of the template’s sections, but was only ${percent(section.none_probability)} sure, so it was put in its likeliest one (${percent(section.mapped_probability)}).`
    case 'parent':
      return 'It goes with its parent section, whose own mapping is doubtful.'
    case 'subsections':
      return 'It takes its subsections’ mapping, which is itself doubtful.'
    case 'extra':
      return `The model was only ${percent(section.mapped_probability)} sure it is this extra section.`
    case 'other':
      return 'Nothing fits it: it is in Other content, the catch-all.'
    default:
      return `The model was only ${percent(section.probability)} sure of this answer.`
  }
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
        {METHOD_TAG[section.method ?? 'model']}
        {short ? '' : `→ ${section.template_heading} (${section.template_title}) · `}
        {!short && section.also_heading
          ? `${section.method === 'appendix' ? 'about' : 'also'} ${section.also_heading} · `
          : ''}
        {percent(section.mapped_probability ?? section.probability)}
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
  if (document.status === 'matched' && document.method === 'sections')
    return (
      <div className={document.doubtful ? 'choice-card compact doubtful' : 'choice-card compact'}>
        <p>
          {document.doubtful && <DoubtBadge />} Mapped onto the{' '}
          <strong>{document.template_title}</strong>, the template its sections fit best
        </p>
        <p>
          The model wasn’t sure which template the whole review follows, so each section was asked
          against every template. {percent(document.sections_fit)} of its text fits this one,{' '}
          {percent(document.sections_margin)} ahead of the next
          {document.doubtful ? ': too close to call, so check the review’s template by hand.' : '.'}
        </p>
      </div>
    )
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
          its template by hand.
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
          {document.doubtful && ' · check the review’s template by hand'}
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
      letter (from its first-token probabilities). A review the model wasn’t sure of (none of the
      templates, or under {percent(DOUBTFUL)}) has each section asked against every template, and
      gets the one its sections fit best. Then every section is given a mapping: the model’s own
      choice; two template sections at once (⇄) when it was torn between two that together it was
      sure of; one of a few <strong>extra sections</strong> (+) many DHRs have but the templates
      don’t name, when asked about a section that fits none or that it was unsure of; its parent
      section’s (↳); its likeliest after “none of these” (≈ nearest fit); for a heading with little
      text of its own, what its subsections mostly got (↥), or “Report part heading”; anything in an
      appendix goes to Appendices (§), keeping what it is about; and what nothing fits goes to Other
      content (…). A section is marked <strong>⚠ Doubtful</strong> when its own mapping is under{' '}
      {percent(DOUBTFUL)} sure (nearest fits and Other content always); a doubtful review template
      is marked on the review. Extra sections sit where reviews most often have them. Prompt{' '}
      {version.prompt_version}.
    </p>
  )

  // The review's sections in its template's order, each template section with its sections.
  const standardisedOutline = review && (
    <ul>
      {review.standardised.map((p, i) => {
        const previous = review.standardised[i - 1]
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
                  ? p.n_opening_blocks > 0
                    ? ''
                    : 'Not in this review'
                  : `${formatNumber(p.n_sections)} section${p.n_sections === 1 ? '' : 's'}`}
                {p.n_opening_blocks > 0 &&
                  `${p.n_sections > 0 ? ' · ' : ''}title page: the text before the first heading`}
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
                    <DoubtBadge title={doubtReason(s)} />
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
              {version.refined_at
                ? `. Not the model’s own single choice: ${[
                    [version.n_combined, 'two sections in one'],
                    [version.n_extra, 'extra sections'],
                    [version.n_parent, 'with their parent'],
                    [version.n_nearest, 'nearest fits'],
                    [version.n_subsections, 'from their subsections'],
                    [version.n_appendix, 'in appendices'],
                    [version.n_other, 'other content'],
                  ]
                    .filter(([n]) => n)
                    .map(([n, what]) => `${formatNumber(n as number)} ${what}`)
                    .join(
                      ', ',
                    )}${version.n_reviews_by_sections ? `. ${formatNumber(version.n_reviews_by_sections)} reviews given the template their sections fit best` : ''}`
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
                            : t.kind === 'extra sections'
                              ? '· for sections the templates lack'
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
                        <DoubtBadge /> Check this by hand. {doubtReason(section)}
                      </p>
                    )}
                    <p className="choice-name">
                      <strong>{chosen.section.heading}</strong>{' '}
                      <span className="muted">in the {chosen.template.title}</span>
                    </p>
                    {methodText(section) && <p className="muted">{methodText(section)}</p>}
                    <p>
                      Confidence{' '}
                      <Confidence p={section.mapped_probability ?? section.probability} />
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
                    ? `Mappings to check by hand: the model was under ${percent(DOUBTFUL)} sure, or the section is a nearest fit or in Other content. (A doubtful review template is shown on the review.)`
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
                                {f.method && f.method !== 'model' && (
                                  <span className="muted">{METHOD_TAG[f.method]}</span>
                                )}
                                {f.heading}
                                {f.also_heading && (
                                  <span className="muted">
                                    {' '}
                                    ({f.method === 'appendix' ? 'about' : 'also'} {f.also_heading})
                                  </span>
                                )}
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
