// Backend response types and shared helpers for the O2 tabs.

// f/dhr/O2/app_backend/get_wikis
export type ReviewRow = {
  doc_id: string
  dataset: string
  n_chars: number
  n_joined: number
  parse_status: string | null
  error_message: string | null
  uploaded_at: string | null
  n_pages: number
}
// The knowledge-base settings a build was made with (f/dhr/O2/kb_settings.py). Builds from
// before 25 Nov 2026 have none: each review went up whole, with WeKnora's own chunking.
export type KnowledgeBaseSettings = {
  chunk_strategy: string
  chunk_size: number
  chunk_overlap: number
  chunk_separators: string[] | null
  ingest_batch_size: number
  ingest_map_parallel: number
  ingest_reduce_parallel: number
  ingest_max_inflight: number
  knowledge_graph: boolean
  graph_instructions: string | null
}
export type Build = {
  instance_id: string
  name: string
  created_by: string
  created_at: string
  finished_at: string | null
  saved_at: string | null
  status: 'created' | 'uploading' | 'building' | 'built' | 'failed'
  status_note: string | null
  dataset: string
  reviews: string
  model: string
  settings: {
    granularity?: string
    template_guidance?: string
    extraction_instructions?: string
    content_instructions?: string
    doc_ids?: string[] | null
    knowledge_base?: KnowledgeBaseSettings
  }
  flow_job_id: string | null
  git_commit: string | null
  n_reviews: number
  n_files: number
  n_pages: number
  n_links: number
  pages_by_type: Record<string, number>
  progress: Progress | null
  review_status: Record<string, number>
  documents: ReviewRow[]
}

// The latest check's live progress (f/dhr/O2/progress.py), kept in wiki.instances.stats.
export type Progress = {
  at: string
  line: string
  percent: number
  reviews_done: number
  reviews_total: number
  reviews_failed: number
  wiki_pending_tasks: number | null
  wiki_active: boolean | null
  pages: number
  model: string | null
}

// f/dhr/O2/app_backend/get_graph
export type PageSummary = {
  slug: string
  title: string
  page_type: string
  summary: string
  n_in: number
  n_out: number
  sources: string[]
}
export type Graph = {
  instance_id: string
  pages: PageSummary[]
  links: { source: string; target: string }[]
  broken_links: number
}

// f/dhr/O2/app_backend/get_page
export type PageLink = { slug: string; title: string | null; page_type: string | null }
export type Page = {
  slug: string
  title: string
  page_type: string
  status: string | null
  summary: string | null
  content: string
  aliases: string[]
  category_path: string[]
  n_chunks_cited: number
  version: number | null
  updated_at: string | null
  links_out: PageLink[]
  links_in: PageLink[]
  sources: string[]
}

// f/dhr/O2/app_backend/get_kgraph: the knowledge graph WeKnora extracts beside the wiki.
// With joining on (f/dhr/O2/entities.py), a named organisation is one node for every review
// that names it (doc_id "Shared by N reviews", docs lists them), and each agency has an "Is a"
// link to a node for its type (is_type, doc_id "Type of organisation").
export type KgNode = {
  id: string
  doc_id: string
  docs: string[]
  name: string
  attributes: string[]
  n_chunks: number
  degree: number
  is_type: boolean
}
export type KnowledgeGraph = {
  nodes: KgNode[]
  links: { source: string; target: string; type: string }[]
  reviews: { doc_id: string; n_nodes: number }[]
  relation_types: Record<string, number>
  total_nodes: number
  truncated: boolean
  joined: boolean
  shared_entities: number
  organisation_types: number
}

// f/dhr/O2/app_backend/get_analysis: saved runs of f/dhr/O2/run_analysis.
export type AnalysisRun = {
  run_id: number
  status: 'running' | 'done' | 'failed'
  engine: string | null
  settings: { engine?: string; join_reviews?: boolean }
  error: string | null
  started_at: string | null
  finished_at: string | null
  nodes: number | null
}
export type AnalysisNode = {
  key: string
  name: string
  docs: string[]
  is_type: boolean
  degree: number
  pagerank: number | null
  betweenness: number | null
  community: number | null
  component: number | null
  similar: { key: string; name: string; score: number }[]
}
export type AnalysisSummary = {
  nodes: number
  links: number
  reviews: number
  components: number
  largest_component: number
  communities: number
  shared_entities?: number
  organisation_types?: number
  top_communities: {
    community: number
    size: number
    reviews: number
    top: string[]
    types: string[]
    relationships: string[]
  }[]
  similar_reviews?: { a: string; b: string; score: number; shared: number; examples: string[] }[]
}
export type AnalysisResponse = {
  runs: AnalysisRun[]
  busy_with: string | null
  has_graph: boolean
  result: { run: AnalysisRun; summary: AnalysisSummary; nodes: AnalysisNode[] } | null
}

// One colour per page type, used the same way in the graph, the legend and the page list.
const TYPE_COLOURS: Record<string, string> = {
  summary: '#0b5e8a',
  entity: '#bf8700',
  concept: '#1a7f37',
  synthesis: '#8250df',
  comparison: '#b42318',
  index: '#444141',
}
const SPARE = ['#0e7c86', '#a8327a', '#5b6b00', '#7a4b00']
export function typeColour(type: string): string {
  if (TYPE_COLOURS[type]) return TYPE_COLOURS[type]
  let h = 0
  for (const c of type) h = (h * 31 + c.charCodeAt(0)) >>> 0
  return SPARE[h % SPARE.length]
}

export const STATUS_TEXT: Record<Build['status'], string> = {
  created: 'Created',
  uploading: 'Uploading reviews',
  building: 'Building',
  built: 'Built',
  failed: 'Failed',
}
export const GUIDANCES: Record<string, string> = {
  'dhr-2016': '2016 DHR guidance',
  'dardr-2026': '2026 DARDR guidance',
  none: 'none (original headings only)',
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

export function formatNumber(n: number | null | undefined): string {
  return n === null || n === undefined ? '—' : n.toLocaleString('en-GB')
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('en-GB', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

// Storage is optional: private windows or quota limits must not stop the app.
export function writeStoredValue<T>(key: string, value: T | null) {
  try {
    if (value === null) localStorage.removeItem(`o2:${key}`)
    else localStorage.setItem(`o2:${key}`, JSON.stringify(value))
  } catch {
    // keep going without persistence
  }
}

export function readStoredValue<T>(key: string): T | null {
  try {
    const text = localStorage.getItem(`o2:${key}`)
    return text ? (JSON.parse(text) as T) : null
  } catch {
    return null
  }
}
