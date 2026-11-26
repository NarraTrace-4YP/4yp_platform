import React, { useEffect, useMemo, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { backend } from './wmill'
import { Build, Graph, Page, PageLink, errorMessage, formatDateTime, typeColour } from './shared'

type Props = { build: Build | null; slug: string | null; onOpenPage: (slug: string) => void }

// WeKnora links pages as [[slug|title]] or [[slug]]; these become in-app links.
function wikiLinksToMarkdown(text: string): string {
  return text.replace(/\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g, (_, slug: string, title?: string) => {
    const label = (title ?? slug).replace(/[[\]]/g, '')
    return `[${label}](#wiki:${encodeURIComponent(slug.trim())})`
  })
}

export default function PagesTab({ build, slug, onOpenPage }: Props) {
  const [graph, setGraph] = useState<Graph | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [type, setType] = useState('')

  useEffect(() => {
    setGraph(null)
    setError(null)
    if (!build) return
    let current = true
    backend
      .graph({ instance_id: build.instance_id })
      .then((g: Graph) => current && setGraph(g))
      .catch((e: unknown) => current && setError(errorMessage(e)))
    return () => {
      current = false
    }
  }, [build?.instance_id, build?.status === 'built'])

  const types = useMemo(
    () => [...new Set((graph?.pages ?? []).map((p) => p.page_type))].sort(),
    [graph]
  )
  const shown = useMemo(() => {
    const q = search.trim().toLowerCase()
    return (graph?.pages ?? []).filter(
      (p) =>
        (!type || p.page_type === type) &&
        (!q || p.title.toLowerCase().includes(q) || p.summary.toLowerCase().includes(q))
    )
  }, [graph, search, type])

  const current = slug ?? shown[0]?.slug ?? null

  if (!build) return <div className="page muted">No wiki chosen yet.</div>
  if (error) return <div className="page error">Couldn’t load the pages: {error}</div>
  if (!graph) return <div className="page muted">Loading the pages…</div>
  if (!graph.pages.length)
    return (
      <div className="page">
        <h2>No pages yet</h2>
        <p>This wiki has no saved pages yet. They appear here at the build’s next check.</p>
      </div>
    )

  return (
    <div className="two-columns">
      <div className="list-column">
        <div className="list-tools">
          <input
            type="search"
            placeholder="Search titles and summaries…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            aria-label="Search pages"
          />
          <select value={type} onChange={(e) => setType(e.target.value)} aria-label="Page type">
            <option value="">Every type</option>
            {types.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
          <p className="muted small">
            {shown.length} of {graph.pages.length} pages
          </p>
        </div>
        <ul className="page-list">
          {shown.map((p) => (
            <li key={p.slug}>
              <button
                className={p.slug === current ? 'page-item selected' : 'page-item'}
                onClick={() => onOpenPage(p.slug)}
              >
                <span className="swatch" style={{ background: typeColour(p.page_type) }} />
                <span>
                  <span className="page-item-title">{p.title}</span>
                  <span className="muted small">
                    {p.page_type} · {p.n_in} in · {p.n_out} out
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      </div>
      <div className="reader-column">
        {current ? (
          <Reader instanceId={build.instance_id} slug={current} onOpenPage={onOpenPage} />
        ) : (
          <p className="muted">No page matches.</p>
        )}
      </div>
    </div>
  )
}

function Reader({ instanceId, slug, onOpenPage }: { instanceId: string; slug: string; onOpenPage: (s: string) => void }) {
  const [page, setPage] = useState<Page | null | undefined>(undefined)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setPage(undefined)
    setError(null)
    let current = true
    backend
      .page({ instance_id: instanceId, slug })
      .then((p: Page | null) => current && setPage(p))
      .catch((e: unknown) => current && setError(errorMessage(e)))
    return () => {
      current = false
    }
  }, [instanceId, slug])

  if (error) return <p className="error">Couldn’t load this page: {error}</p>
  if (page === undefined) return <p className="muted">Loading…</p>
  if (page === null) return <p className="muted">This page isn’t in the saved copy (it may have been renamed).</p>

  const linkList = (links: PageLink[]) => (
    <ul className="link-list">
      {links.map((l) => (
        <li key={l.slug}>
          {l.title ? (
            <button className="link-button" onClick={() => onOpenPage(l.slug)}>
              <span className="swatch" style={{ background: typeColour(l.page_type ?? '') }} /> {l.title}
            </button>
          ) : (
            <span className="muted">{l.slug} (missing)</span>
          )}
        </li>
      ))}
    </ul>
  )

  return (
    <article className="reader">
      <p className="small" style={{ color: typeColour(page.page_type) }}>
        {page.page_type}
        {page.category_path?.length ? ` · ${page.category_path.join(' / ')}` : ''}
      </p>
      <h2>{page.title}</h2>
      {page.aliases?.length > 0 && <p className="muted small">Also: {page.aliases.join(', ')}</p>}
      <div className="markdown">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            a: ({ href, children }) =>
              href?.startsWith('#wiki:') ? (
                <button
                  className="link-button wiki-link"
                  onClick={() => onOpenPage(decodeURIComponent(href.slice(6)))}
                >
                  {children}
                </button>
              ) : (
                <a href={href} target="_blank" rel="noreferrer">
                  {children}
                </a>
              ),
          }}
        >
          {wikiLinksToMarkdown(page.content)}
        </ReactMarkdown>
      </div>
      <div className="reader-meta">
        <div>
          <h4>Built from</h4>
          {page.sources.length ? (
            <ul className="link-list">
              {page.sources.map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ul>
          ) : (
            <p className="muted small">No review recorded.</p>
          )}
          <p className="muted small">
            {page.n_chunks_cited} passage{page.n_chunks_cited === 1 ? '' : 's'} cited · version{' '}
            {page.version ?? '—'} · {formatDateTime(page.updated_at)}
          </p>
        </div>
        <div>
          <h4>Links to ({page.links_out.length})</h4>
          {linkList(page.links_out)}
        </div>
        <div>
          <h4>Linked from ({page.links_in.length})</h4>
          {linkList(page.links_in)}
        </div>
      </div>
    </article>
  )
}
