import React, { useEffect, useMemo, useRef, useState } from 'react'
import {
  forceCenter,
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
  SimulationNodeDatum,
} from 'd3-force'
import { backend } from './wmill'
import { Build, Graph, PageSummary, errorMessage, typeColour } from './shared'

type Node = SimulationNodeDatum & PageSummary & { degree: number; r: number }
type Edge = { source: Node; target: Node }
type Props = { build: Build | null; onOpenPage: (slug: string) => void }

const SIZE = 1000 // layout space; the view scales it to fit

// A point on screen, in the graph's own coordinates (whatever the view's fit and size).
function toLayout(svg: SVGSVGElement, clientX: number, clientY: number): [number, number] | null {
  const matrix = svg.getScreenCTM()
  if (!matrix) return null
  const point = new DOMPoint(clientX, clientY).matrixTransform(matrix.inverse())
  return [point.x, point.y]
}

export default function GraphTab({ build, onOpenPage }: Props) {
  const [graph, setGraph] = useState<Graph | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [hidden, setHidden] = useState<Set<string>>(new Set())
  const [hideLonely, setHideLonely] = useState(false)
  const [search, setSearch] = useState('')
  const [hover, setHover] = useState<string | null>(null)
  const [chosen, setChosen] = useState<string | null>(null)
  const [view, setView] = useState({ x: 0, y: 0, k: 1 })
  const svgRef = useRef<SVGSVGElement>(null)
  const drag = useRef<{ x: number; y: number } | null>(null)
  const [reloads, setReloads] = useState(0)

  useEffect(() => {
    setGraph(null)
    setChosen(null)
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
    // Not on every save: a growing wiki would keep moving under the reader. Reload instead.
  }, [build?.instance_id, reloads])

  const types = useMemo(() => {
    const counts: Record<string, number> = {}
    for (const p of graph?.pages ?? []) counts[p.page_type] = (counts[p.page_type] ?? 0) + 1
    return Object.entries(counts).sort((a, b) => b[1] - a[1])
  }, [graph])

  // Lay the graph out once per wiki and filter: a fixed number of simulation steps, then a
  // still picture (no jiggling, and the same picture each time for the same wiki).
  const layout = useMemo(() => {
    if (!graph) return null
    let pages = graph.pages.filter((p) => !hidden.has(p.page_type))
    if (hideLonely) pages = pages.filter((p) => p.n_in + p.n_out > 0)
    const bySlug = new Map<string, Node>()
    const nodes: Node[] = pages.map((p, i) => {
      const angle = i * 2.399963 // golden angle: an even, repeatable start
      const radius = 12 * Math.sqrt(i)
      const node: Node = {
        ...p,
        degree: 0,
        r: 3,
        x: SIZE / 2 + radius * Math.cos(angle),
        y: SIZE / 2 + radius * Math.sin(angle),
      }
      bySlug.set(p.slug, node)
      return node
    })
    const edges: Edge[] = []
    for (const l of graph.links) {
      const s = bySlug.get(l.source)
      const t = bySlug.get(l.target)
      if (s && t) {
        edges.push({ source: s, target: t })
        s.degree++
        t.degree++
      }
    }
    for (const n of nodes) n.r = 3 + Math.sqrt(n.degree) * 1.6
    const sim = forceSimulation(nodes)
      .force(
        'link',
        forceLink<Node, Edge>(edges)
          .distance(40)
          .strength(0.25)
      )
      .force('charge', forceManyBody().strength(-45).distanceMax(350))
      .force('collide', forceCollide<Node>((d) => d.r + 2))
      .force('x', forceX(SIZE / 2).strength(0.04))
      .force('y', forceY(SIZE / 2).strength(0.04))
      .force('center', forceCenter(SIZE / 2, SIZE / 2))
      .stop()
    const ticks = nodes.length > 1500 ? 160 : 300
    for (let i = 0; i < ticks; i++) sim.tick()
    const neighbours = new Map<string, Set<string>>()
    for (const e of edges) {
      if (!neighbours.has(e.source.slug)) neighbours.set(e.source.slug, new Set())
      if (!neighbours.has(e.target.slug)) neighbours.set(e.target.slug, new Set())
      neighbours.get(e.source.slug)!.add(e.target.slug)
      neighbours.get(e.target.slug)!.add(e.source.slug)
    }
    const labelled = new Set(
      [...nodes].sort((a, b) => b.degree - a.degree).slice(0, 25).map((n) => n.slug)
    )
    // The view starts fitted to the pages, with room for labels.
    const xs = nodes.map((n) => n.x ?? 0)
    const ys = nodes.map((n) => n.y ?? 0)
    const pad = 60
    const box = {
      x: Math.min(...xs, SIZE / 2) - pad,
      y: Math.min(...ys, SIZE / 2) - pad,
      w: Math.max(Math.max(...xs, SIZE / 2) - Math.min(...xs, SIZE / 2), 200) + 2 * pad,
      h: Math.max(Math.max(...ys, SIZE / 2) - Math.min(...ys, SIZE / 2), 200) + 2 * pad,
    }
    return { nodes, edges, neighbours, labelled, bySlug, box }
  }, [graph, hidden, hideLonely])

  useEffect(() => setView({ x: 0, y: 0, k: 1 }), [layout])

  const query = search.trim().toLowerCase()
  const matches = useMemo(() => {
    if (!layout || !query) return null
    return new Set(layout.nodes.filter((n) => n.title.toLowerCase().includes(query)).map((n) => n.slug))
  }, [layout, query])

  const focus = hover ?? chosen
  const near = focus ? layout?.neighbours.get(focus) ?? new Set<string>() : null
  const lit = (slug: string) => {
    if (matches) return matches.has(slug)
    if (!focus) return true
    return slug === focus || near!.has(slug)
  }
  const chosenNode = chosen ? layout?.bySlug.get(chosen) : undefined

  function onWheel(e: React.WheelEvent) {
    const svg = svgRef.current
    if (!svg) return
    const point = toLayout(svg, e.clientX, e.clientY)
    if (!point) return
    const [px, py] = point
    const k = Math.min(8, Math.max(0.5, view.k * (e.deltaY < 0 ? 1.15 : 1 / 1.15)))
    setView({ k, x: px - ((px - view.x) * k) / view.k, y: py - ((py - view.y) * k) / view.k })
  }
  function onPointerDown(e: React.PointerEvent) {
    if ((e.target as Element).closest('.node')) return
    drag.current = { x: e.clientX, y: e.clientY }
    ;(e.currentTarget as Element).setPointerCapture(e.pointerId)
  }
  function onPointerMove(e: React.PointerEvent) {
    if (!drag.current || !svgRef.current) return
    const a = toLayout(svgRef.current, drag.current.x, drag.current.y)
    const b = toLayout(svgRef.current, e.clientX, e.clientY)
    if (!a || !b) return
    const dx = b[0] - a[0]
    const dy = b[1] - a[1]
    drag.current = { x: e.clientX, y: e.clientY }
    setView((v) => ({ ...v, x: v.x + dx, y: v.y + dy }))
  }

  if (!build) return <div className="page muted">No wiki chosen yet.</div>
  if (error) return <div className="page error">Couldn’t load the graph: {error}</div>
  if (!graph || !layout) return <div className="page muted">Laying out the graph…</div>
  if (!graph.pages.length)
    return (
      <div className="page">
        <h2>No pages yet</h2>
        <p>This wiki has no saved pages yet. They appear here at the build’s next check.</p>
      </div>
    )

  return (
    <div className="graph-view">
      <div className="graph-tools">
        <input
          type="search"
          placeholder="Find a page…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          aria-label="Find a page"
        />
        <ul className="legend">
          {types.map(([t, n]) => (
            <li key={t}>
              <label>
                <input
                  type="checkbox"
                  checked={!hidden.has(t)}
                  onChange={() =>
                    setHidden((h) => {
                      const next = new Set(h)
                      if (next.has(t)) next.delete(t)
                      else next.add(t)
                      return next
                    })
                  }
                />
                <span className="swatch" style={{ background: typeColour(t) }} /> {t}{' '}
                <span className="muted">{n}</span>
              </label>
            </li>
          ))}
        </ul>
        <label className="small">
          <input type="checkbox" checked={hideLonely} onChange={(e) => setHideLonely(e.target.checked)} />{' '}
          Hide pages with no links
        </label>
        <p className="muted small">
          {layout.nodes.length} pages · {layout.edges.length} links
          {graph.broken_links > 0 && ` · ${graph.broken_links} links to missing pages`}. Scroll to
          zoom, drag to move, click a page to see it.
        </p>
        {(build.status === 'building' || build.status === 'uploading') && (
          <button className="link-button small" onClick={() => setReloads((n) => n + 1)}>
            Reload the graph (the wiki is still growing)
          </button>
        )}
      </div>
      <div className="graph-stage">
        <svg
          ref={svgRef}
          viewBox={`${layout.box.x} ${layout.box.y} ${layout.box.w} ${layout.box.h}`}
          preserveAspectRatio="xMidYMid meet"
          onWheel={onWheel}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={() => (drag.current = null)}
          onClick={(e) => {
            if (!(e.target as Element).closest('.node')) setChosen(null)
          }}
          role="img"
          aria-label="Graph of the wiki’s pages and their links"
        >
          <g transform={`translate(${view.x},${view.y}) scale(${view.k})`}>
            <g className="edges">
              {layout.edges.map((e, i) => {
                const on = focus && (e.source.slug === focus || e.target.slug === focus)
                return (
                  <line
                    key={i}
                    x1={e.source.x}
                    y1={e.source.y}
                    x2={e.target.x}
                    y2={e.target.y}
                    className={on ? 'edge on' : focus || matches ? 'edge faint' : 'edge'}
                    strokeWidth={1 / view.k}
                  />
                )
              })}
            </g>
            {layout.nodes.map((n) => (
              <g
                key={n.slug}
                className={lit(n.slug) ? 'node' : 'node dim'}
                transform={`translate(${n.x},${n.y})`}
                onPointerEnter={() => setHover(n.slug)}
                onPointerLeave={() => setHover(null)}
                onClick={() => setChosen(n.slug)}
              >
                <circle
                  r={n.r}
                  fill={typeColour(n.page_type)}
                  stroke={n.slug === chosen ? '#201e1d' : '#fff'}
                  strokeWidth={(n.slug === chosen ? 2 : 0.8) / view.k}
                />
                {(layout.labelled.has(n.slug) || n.slug === focus || matches?.has(n.slug) || view.k > 2.5) &&
                  lit(n.slug) && (
                    <text x={n.r + 3 / view.k} y={4 / view.k} fontSize={11 / view.k}>
                      {n.title}
                    </text>
                  )}
              </g>
            ))}
          </g>
        </svg>
        {chosenNode && (
          <aside className="graph-card">
            <p className="small" style={{ color: typeColour(chosenNode.page_type) }}>
              {chosenNode.page_type}
            </p>
            <h3>{chosenNode.title}</h3>
            {chosenNode.summary && <p>{chosenNode.summary}</p>}
            <p className="muted small">
              {chosenNode.n_out} links out · {chosenNode.n_in} links in
              {chosenNode.sources.length > 0 &&
                ` · from ${chosenNode.sources.length} review${chosenNode.sources.length > 1 ? 's' : ''}`}
            </p>
            <button className="button" onClick={() => onOpenPage(chosenNode.slug)}>
              Read the page
            </button>
          </aside>
        )}
      </div>
    </div>
  )
}
