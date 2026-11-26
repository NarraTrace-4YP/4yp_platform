// Backend response types and shared display/storage helpers for the O1 tabs.

// The datasets in f/dhr/datasets.py (a test checks they agree).
export const DATASETS = ['DHRC-100', 'DHRC-RAW']
export const LIBRARY = 'https://homicide-review.homeoffice.gov.uk'

// f/dhr/O1/app_backend/get_status
export type Coverage = {
  n_listed: number
  n_loaded: number
  n_home_office: number
  n_halt: number
  n_elsewhere: number
  n_no_link: number
  n_in_library: number
  n_with_details: number
}
// Where a dataset's section structure comes from (the "headings" setting in datasets.py).
export type Headings = 'checked' | 'datalab'
export const HEADINGS: Record<Headings, string> = {
  checked: 'the headings the team checked by hand',
  datalab: 'Datalab’s own headings (nobody has checked them)',
}
export type SectionsStatus = {
  headings: Headings
  grouped_at: string
  git_commit: string | null
  n_reviews: number
  n_sections: number
  n_blocks: number
  n_skipped: number
  n_across_pages: number
}
// The guidance editions whose templates sections can be matched against (f/dhr/O1/find_candidate_template_sections/templates.py).
export const GUIDANCES: Record<string, string> = {
  'dhr-2016': '2016 DHR guidance',
  'dardr-2026': '2026 DARDR guidance',
}
// The template candidates of a dataset's newest grouped version against one guidance
// (f/dhr/O1/find_candidate_template_sections/find_candidate_template_sections).
export type CandidatesStatus = {
  guidance: string
  matched_at: string
  git_commit: string | null
  model: string
  k: number
  n_sections: number
  n_candidates: number
  n_with_heading_match: number
}
// The template choices (step 5) of a dataset's newest grouped version against one guidance
// (f/dhr/O1/choose_template_section/choose_template_section).
export type ChoicesStatus = {
  guidance: string
  reviews: 'pilot' | 'all'
  chosen_at: string
  git_commit: string | null
  model: string
  prompt_version: string
  n_reviews: number
  n_reviews_matched: number // reviews given a template
  n_sections: number
  n_matched: number
  n_unmatched: number
  n_no_answer: number
  n_reviews_doubtful: number
  n_doubtful: number // sections to check by hand
}
export type DatasetStatus = {
  name: string
  repo_id: string
  expected_reviews: number
  strict: boolean
  headings: Headings
  sections: SectionsStatus | null
  candidates: CandidatesStatus[]
  choices: ChoicesStatus[]
  version: {
    revision: string
    repo_id: string
    loaded_at: string
    git_commit: string | null
    n_reviews: number
    n_pages: number
    n_blocks: number
  } | null
  coverage: Coverage
}
export type Status = {
  datasets: DatasetStatus[]
  case_metadata: {
    snapshot_id: number
    collected_at: string
    git_commit: string | null
    n_reviews: number
    n_filters: number
    n_details: number
    n_reviews_with_details: number
  } | null
  // Each guidance whose templates are stored (the first candidates run against it stores them).
  templates: {
    guidance: string
    title: string
    source_url: string
    loaded_at: string
    n_templates: number
    n_sections: number
  }[]
  code_version: string
  windmill_url: string | null
  workspace: string | null
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

export function formatNumber(n: number | null | undefined): string {
  return n === null || n === undefined ? '—' : n.toLocaleString('en-GB')
}

export function formatPercent(part: number, whole: number): string {
  if (!whole) return '—'
  const value = (100 * part) / whole
  return `${value < 10 && value > 0 ? value.toFixed(1) : Math.round(value)}%`
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const date = new Date(iso)
  return date.toLocaleString('en-GB', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function shortenId(id: string | null | undefined): string {
  if (!id) return '—'
  return id.startsWith('local-') ? id : id.slice(0, 8)
}

// Storage is optional: private windows or quota limits must not prevent running the app.
export function writeStoredValue<T>(key: string, value: T | null) {
  try {
    if (value === null) localStorage.removeItem(`o1:${key}`)
    else localStorage.setItem(`o1:${key}`, JSON.stringify(value))
  } catch {
    // Keep the in-memory state usable when the browser refuses persistence.
  }
}

export function readStoredValue<T>(key: string): T | null {
  try {
    const text = localStorage.getItem(`o1:${key}`)
    return text ? (JSON.parse(text) as T) : null
  } catch {
    return null
  }
}
