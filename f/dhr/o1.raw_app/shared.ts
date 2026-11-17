// Types for what the backend scripts return, and small helpers every tab uses.

// The datasets in f/dhr/datasets.py (a test checks they agree).
export const DATASETS = ['DHRC-100', 'DHRC-RAW']
export const LIBRARY = 'https://homicide-review.homeoffice.gov.uk'

// f/dhr/o1_status
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
export type DatasetStatus = {
  name: string
  repo_id: string
  expected_reviews: number
  strict: boolean
  headings: Headings
  sections: SectionsStatus | null
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
  code_version: string
  windmill_url: string | null
  workspace: string | null
}

// f/dhr/o1_run_status
export type RunState = 'queued' | 'running' | 'done' | 'failed' | 'cancelled'
export type RunStatus = {
  job_id: string
  path: string
  state: RunState
  created_at: string | null
  started_at: string | null
  duration_ms: number | null
  progress: number | null
  log: string[]
  result: any
  error: string | null
  steps: { id: string; state: string; job_id: string | null }[] | null
  current_step?: string | null
}

export function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

export function number(n: number | null | undefined): string {
  return n === null || n === undefined ? '—' : n.toLocaleString('en-GB')
}

export function percent(part: number, whole: number): string {
  if (!whole) return '—'
  const value = (100 * part) / whole
  return `${value < 10 && value > 0 ? value.toFixed(1) : Math.round(value)}%`
}

export function when(iso: string | null | undefined): string {
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

export function shortId(id: string | null | undefined): string {
  if (!id) return '—'
  return id.startsWith('local-') ? id : id.slice(0, 8)
}

export function minutes(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '—'
  const seconds = Math.round(ms / 1000)
  if (seconds < 60) return `${seconds} s`
  return `${Math.floor(seconds / 60)} min ${seconds % 60} s`
}

// Links into Windmill, when the server told us its address.
export function windmillLink(status: Status | null, path: string): string | null {
  if (!status?.windmill_url) return null
  const workspace = status.workspace ? `?workspace=${status.workspace}` : ''
  return `${status.windmill_url.replace(/\/$/, '')}${path}${workspace}`
}

// Browser storage can be missing or refuse (private windows), so never let it break the app.
export function remember<T>(key: string, value: T | null) {
  try {
    if (value === null) localStorage.removeItem(`o1:${key}`)
    else localStorage.setItem(`o1:${key}`, JSON.stringify(value))
  } catch {
    // nothing to do: the app works without it
  }
}

export function recall<T>(key: string): T | null {
  try {
    const text = localStorage.getItem(`o1:${key}`)
    return text ? (JSON.parse(text) as T) : null
  } catch {
    return null
  }
}
