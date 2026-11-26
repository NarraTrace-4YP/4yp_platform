// Fix (25 Nov 2026, problem review "O2 graph viewer is hard to read"):
//   Problem: the wiki graph was one tangled ball. A generic force layout pulled every page
//   towards the middle, so separate reviews and topics overlapped, labels piled on top of
//   each other, and nothing said which part of the graph was about what.
//   Why: d3-force with a shared centre and fixed label rules (the 25 best-linked pages)
//   ignores the graph's shape: unconnected groups and lonely pages all collapse together.
//   Solution: draw it the way WeKnora's own wiki graph does, with WeKnora's layout code
//   (wikiGraphLayout.ts, MIT, copied unchanged): each connected group is laid out on its
//   own and outlined, its main themes are named with captions you can click to zoom in,
//   pages with no links sit on a shelf below, labels are placed so they never overlap,
//   and the layout settles with a short animation. Hovering a page lights up its
//   neighbours, dragging pins it, and a list of groups lets you jump between them.
import React, { useEffect, useMemo, useRef, useState } from 'react'
import { backend } from './wmill'
import { Build, Graph, PageSummary, errorMessage, typeColour } from './shared'
import {
  buildGraphLayout,
  graphClusterOutline,
  graphLandmarkTitle,
  graphNodeRadius,
  graphPointBounds,
  initialGraphViewport,
  stepGraphLayout,
  visibleGraphLabels,
  type ForceNode,
  type GraphCluster,
  type GraphLabel,
  type GraphLink,
} from './wikiGraphLayout'

type Props = { build: Build | null; onOpenPage: (slug: string) => void }
type Group = { key: string; name: string; count: number }

const SVG = 'http://www.w3.org/2000/svg'
const MIN_SCALE = 0.08
const MAX_SCALE = 4

/** What the side panel can ask the drawing to do. */
type GraphApi = {
  fit: () => void
  zoom: (factor: number) => void
  show: (slug: string) => void
  focusGroup: (key: string | null) => void
  setMatches: (slugs: Set<string> | null) => void
  select: (slug: string | null) => void
  dispose: () => void
}

type Callbacks = {
  onSelect: (slug: string | null) => void
  onOpen: (slug: string) => void
  onFocus: (key: string | null) => void
}

type DrawnNode = ForceNode & {
  title: string
  type: string
  g: SVGGElement
  label: SVGTextElement
  labelWidth: number
}
type DrawnEdge = { line: SVGLineElement; source: DrawnNode; target: DrawnNode }

function el<K extends keyof SVGElementTagNameMap>(
  name: K,
  attrs: Record<string, string | number> = {},
  parent?: Element
): SVGElementTagNameMap[K] {
  const node = document.createElementNS(SVG, name)
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v))
  if (parent) parent.appendChild(node)
  return node
}

function shorten(text: string, n: number): string {
  return text.length > n ? text.slice(0, n - 1) + '…' : text
}

/** Draws the pages and links into container and keeps them moving until they settle. */
function drawGraph(
  container: HTMLDivElement,
  pages: PageSummary[],
  links: GraphLink[],
  on: Callbacks
): { api: GraphApi; groups: Group[] } {
  const width = container.clientWidth || 900
  const height = container.clientHeight || 600
  const pageBySlug = new Map(pages.map((p) => [p.slug, p]))
  const edgesIn = links.filter(
    (l) => l.source !== l.target && pageBySlug.has(l.source) && pageBySlug.has(l.target)
  )
  const layout = buildGraphLayout(
    pages.map((p) => ({
      slug: p.slug,
      link_count: p.n_in + p.n_out,
      page_type: p.page_type,
      title: p.title,
    })),
    edgesIn,
    width,
    height
  )

  const adjacency = new Map<string, Set<string>>(pages.map((p) => [p.slug, new Set()]))
  for (const e of edgesIn) {
    adjacency.get(e.source)!.add(e.target)
    adjacency.get(e.target)!.add(e.source)
  }

  // --- the picture's layers
  const svg = el('svg', { viewBox: `0 0 ${width} ${height}`, class: 'wg' })
  svg.setAttribute('role', 'img')
  svg.setAttribute('aria-label', 'Graph of the wiki’s pages and their links')
  const defs = el('defs', {}, svg)
  for (const [id, d, refX] of [
    ['wg-end', 'M0,0 L10,3 L0,6 L2,3 Z', 10],
    ['wg-start', 'M10,0 L0,3 L10,6 L8,3 Z', 0],
    ['wg-end-on', 'M0,0 L10,3 L0,6 L2,3 Z', 10],
    ['wg-start-on', 'M10,0 L0,3 L10,6 L8,3 Z', 0],
  ] as const) {
    const marker = el(
      'marker',
      { id, viewBox: '0 0 10 6', refX, refY: 3, markerWidth: 8, markerHeight: 6, orient: 'auto' },
      defs
    )
    el('path', { d, class: id.endsWith('-on') ? 'wg-arrow on' : 'wg-arrow' }, marker)
  }
  const root = el('g', { class: 'wg-root' }, svg)
  const outlineLayer = el('g', {}, root)
  const sectionLayer = el('g', { 'pointer-events': 'none' }, root)
  const edgeLayer = el('g', { 'pointer-events': 'none' }, root)
  const nodeLayer = el('g', {}, root)
  const captionLayer = el('g', {}, root)
  container.replaceChildren(svg)

  // --- groups: an outline round each connected group, and names for its main themes
  const outlines: { path: SVGPathElement; cluster: GraphCluster }[] = []
  const captions: {
    g: SVGGElement
    cluster: GraphCluster
    anchor: string
    width: number
    priority: number
  }[] = []
  const groups: Group[] = []
  for (const cluster of layout.clusters) {
    const path = el('path', { class: 'wg-outline', 'data-cluster': cluster.representative }, outlineLayer)
    outlines.push({ path, cluster })
    if (cluster.slugs.length < 5) continue
    const name = graphLandmarkTitle(pageBySlug.get(cluster.anchors[0])!.title)
    groups.push({ key: cluster.representative, name, count: cluster.slugs.length })
    cluster.anchors.forEach((anchor, rank) => {
      const g = el('g', { class: 'wg-caption', tabindex: 0, role: 'button' }, captionLayer)
      const pill = el('rect', { class: 'wg-caption-pill', rx: 12, height: 24, y: -17 }, g)
      const text = el('text', { 'text-anchor': 'middle' }, g)
      text.textContent = shorten(graphLandmarkTitle(pageBySlug.get(anchor)!.title), 24)
      const w = (text.getComputedTextLength() || text.textContent.length * 8) + 24
      pill.setAttribute('x', String(-w / 2))
      pill.setAttribute('width', String(w))
      g.setAttribute('aria-label', `Zoom in on the group around ${text.textContent}`)
      const go = (e: Event) => {
        e.stopPropagation()
        focusGroup(focused === cluster.representative ? null : cluster.representative)
      }
      g.addEventListener('click', go)
      g.addEventListener('pointerdown', (e) => e.stopPropagation())
      g.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') go(e)
      })
      captions.push({ g, cluster, anchor, width: w, priority: 100000 + cluster.slugs.length - rank })
    })
  }
  groups.sort((a, b) => b.count - a.count)
  const sections = layout.sections.map((section) => {
    const text = el('text', { x: section.left + 16, class: 'wg-section' }, sectionLayer)
    text.textContent =
      section.kind === 'unlinked'
        ? `Pages with no links (${section.count})`
        : `Pages whose links are all hidden here (${section.count})`
    return { text, top: section.top }
  })

  // --- pages
  const nodes: DrawnNode[] = []
  const nodeBySlug = new Map<string, DrawnNode>()
  for (const page of pages) {
    const placement = layout.placements.get(page.slug)!
    const linkCount = page.n_in + page.n_out
    const r = graphNodeRadius(linkCount)
    const g = el('g', { class: 'wg-node', 'data-slug': page.slug }, nodeLayer)
    el('circle', { r: r + 5, class: 'wg-ring', stroke: typeColour(page.page_type) }, g)
    el('circle', { r, class: 'wg-dot', fill: typeColour(page.page_type) }, g)
    const label = el('text', { 'text-anchor': 'middle', class: 'wg-label' }, g)
    label.textContent = shorten(page.title, 28)
    const node: DrawnNode = {
      slug: page.slug,
      x: placement.x,
      y: placement.y,
      vx: 0,
      vy: 0,
      linkCount,
      pinned: false,
      title: page.title,
      type: page.page_type,
      g,
      label,
      labelWidth: 0,
    }
    nodes.push(node)
    nodeBySlug.set(page.slug, node)
  }
  for (const n of nodes) n.labelWidth = n.label.getComputedTextLength() || n.title.length * 7

  // --- links: a link both ways is one line with two arrows
  const directed = new Set(edgesIn.map((e) => `${e.source}\u0000${e.target}`))
  const seen = new Set<string>()
  const edges: DrawnEdge[] = []
  for (const e of edgesIn) {
    const key = [e.source, e.target].sort().join('\u0000')
    if (seen.has(key)) continue
    seen.add(key)
    const both = directed.has(`${e.target}\u0000${e.source}`)
    const line = el('line', { class: both ? 'wg-edge both' : 'wg-edge' }, edgeLayer)
    edges.push({ line, source: nodeBySlug.get(e.source)!, target: nodeBySlug.get(e.target)! })
  }
  const edgesOf = new Map<string, DrawnEdge[]>()
  for (const e of edges) {
    for (const s of [e.source.slug, e.target.slug]) {
      if (!edgesOf.has(s)) edgesOf.set(s, [])
      edgesOf.get(s)!.push(e)
    }
  }
  const important = new Set(
    [...nodes]
      .sort((a, b) => b.linkCount - a.linkCount)
      .slice(0, Math.max(1, Math.min(12, Math.ceil(nodes.length / 30))))
      .map((n) => n.slug)
  )

  // --- state
  let scale = 1
  let tx = 0
  let ty = 0
  let hover: string | null = null
  let selected: string | null = null
  let matches: Set<string> | null = null
  let focused: string | null = null
  let focusedSlugs = new Set<string>()
  let arrows = true
  let frame = 0
  let flight = 0
  let labelsQueued = 0
  let alpha = 1
  let ticks = 0

  function placeEdge(e: DrawnEdge) {
    const dx = e.target.x - e.source.x
    const dy = e.target.y - e.source.y
    const d = Math.hypot(dx, dy) || 1
    const gap = arrows ? 4 : 0
    const rs = graphNodeRadius(e.source.linkCount) + gap
    const rt = graphNodeRadius(e.target.linkCount) + gap
    e.line.setAttribute('x1', String(e.source.x + (dx / d) * rs))
    e.line.setAttribute('y1', String(e.source.y + (dy / d) * rs))
    e.line.setAttribute('x2', String(e.target.x - (dx / d) * rt))
    e.line.setAttribute('y2', String(e.target.y - (dy / d) * rt))
  }
  function placeNode(n: DrawnNode) {
    n.g.setAttribute('transform', `translate(${n.x},${n.y})`)
  }
  function placeAll() {
    for (const n of nodes) placeNode(n)
    for (const e of edges) placeEdge(e)
  }

  // Which labels show: group names when zoomed out, page names when zoomed in, the page
  // under the pointer, the chosen page and search matches always; none may overlap.
  function refreshLabels() {
    labelsQueued = 0
    const wantArrows = scale >= 0.8
    if (wantArrows !== arrows) {
      arrows = wantArrows
      root.classList.toggle('no-arrows', !arrows)
      for (const e of edges) placeEdge(e)
    }
    for (const s of sections) {
      s.text.setAttribute('font-size', String(13 / scale))
      s.text.setAttribute('y', String(s.top - 12 / scale))
    }
    for (const { path, cluster } of outlines) {
      const members = cluster.slugs.map((s) => nodeBySlug.get(s)!)
      path.setAttribute(
        'd',
        graphClusterOutline(
          members.map((n) => ({ x: n.x, y: n.y, radius: graphNodeRadius(n.linkCount) })),
          scale
        )
      )
      path.classList.toggle('dim', !!focused && focused !== cluster.representative)
    }
    const wanted: GraphLabel[] = []
    for (const c of captions) {
      const anchor = nodeBySlug.get(c.anchor)!
      const outside = !!focused && focused !== c.cluster.representative
      c.g.classList.toggle('outside', outside)
      const show = outside ? c.anchor === c.cluster.anchors[0] : scale < 1.2
      if (!show) continue
      const lx = anchor.x
      const ly = anchor.y + graphNodeRadius(anchor.linkCount) + 18 / scale
      c.g.setAttribute('transform', `translate(${lx},${ly}) scale(${1 / scale})`)
      const x = lx * scale
      const y = ly * scale
      wanted.push({
        slug: `group:${c.anchor}`,
        priority: c.priority,
        required: outside,
        left: x - c.width / 2 - 8,
        right: x + c.width / 2 + 8,
        top: y - 16,
        bottom: y + 5,
      })
    }
    const captioned = new Set(wanted.map((w) => w.slug.slice(6)))
    for (const n of nodes) {
      const required = n.slug === selected || n.slug === hover || !!matches?.has(n.slug)
      if (captioned.has(n.slug) && !required) continue
      const detail = scale >= 0.5 || (focusedSlugs.has(n.slug) && scale >= 0.4)
      const landmark = important.has(n.slug) && (captions.length === 0 || scale >= 0.4)
      if (!required && !detail && !landmark) continue
      const half = (n.labelWidth * 12) / 11 / 2 + 5
      const base = (n.y + graphNodeRadius(n.linkCount)) * scale + 16
      wanted.push({
        slug: n.slug,
        priority: n.linkCount,
        required,
        left: n.x * scale - half,
        right: n.x * scale + half,
        top: base - 15,
        bottom: base + 4,
      })
    }
    const visible = visibleGraphLabels(wanted)
    for (const c of captions) c.g.style.display = visible.has(`group:${c.anchor}`) ? '' : 'none'
    for (const n of nodes) {
      const shown = visible.has(n.slug)
      n.label.style.display = shown ? '' : 'none'
      if (shown) {
        n.label.setAttribute('font-size', String(12 / scale))
        n.label.setAttribute('dy', String(graphNodeRadius(n.linkCount) + 16 / scale))
      }
    }
  }
  function queueLabels() {
    if (!labelsQueued) labelsQueued = requestAnimationFrame(refreshLabels)
  }

  function apply() {
    root.setAttribute('transform', `translate(${tx},${ty}) scale(${scale})`)
    queueLabels()
  }
  function flyTo(x: number, y: number, s: number, ms = 450) {
    cancelAnimationFrame(flight)
    const from = { x: tx, y: ty, s: scale }
    const start = performance.now()
    const step = (now: number) => {
      const t = Math.min(1, (now - start) / ms)
      const ease = 1 - Math.pow(1 - t, 3)
      tx = from.x + (x - from.x) * ease
      ty = from.y + (y - from.y) * ease
      scale = from.s + (s - from.s) * ease
      apply()
      if (t < 1) flight = requestAnimationFrame(step)
    }
    flight = requestAnimationFrame(step)
  }
  function size() {
    return { w: container.clientWidth || width, h: container.clientHeight || height }
  }
  function frameNodes(list: { x: number; y: number }[], most = 1.75) {
    const { w, h } = size()
    const b = graphPointBounds(list)
    const s = Math.min(
      most,
      Math.max(
        MIN_SCALE,
        Math.min((w - 160) / Math.max(100, b.right - b.left), (h - 160) / Math.max(100, b.bottom - b.top))
      )
    )
    flyTo(w / 2 - ((b.left + b.right) / 2) * s, h / 2 - ((b.top + b.bottom) / 2) * s, s)
  }

  // Hovering or choosing a page lights it and its neighbours; a search lights its matches.
  function highlight() {
    const centre = hover ?? selected
    const lit = matches
      ? matches
      : centre
        ? new Set([centre, ...(adjacency.get(centre) ?? [])])
        : null
    for (const n of nodes) {
      n.g.classList.toggle('dim', !!lit && !lit.has(n.slug))
      n.g.classList.toggle('chosen', n.slug === selected)
    }
    for (const e of edges) {
      const on = !!centre && !matches && (e.source.slug === centre || e.target.slug === centre)
      e.line.classList.toggle('on', on)
      e.line.classList.toggle('dim', !!lit && !on)
    }
    queueLabels()
  }

  function focusGroup(key: string | null) {
    focused = key
    const cluster = layout.clusters.find((c) => c.representative === key)
    focusedSlugs = new Set(cluster?.slugs ?? [])
    on.onFocus(key)
    if (cluster) frameNodes(cluster.slugs.map((s) => nodeBySlug.get(s)!))
    queueLabels()
  }

  // --- the simulation settles over a few seconds; dragging a page wakes it again
  function tick() {
    alpha *= 0.985
    if (alpha < 0.02) {
      frame = 0
      refreshLabels()
      return
    }
    stepGraphLayout(nodes, edgesIn, layout.placements, alpha)
    placeAll()
    if (++ticks % 6 === 0) queueLabels()
    frame = requestAnimationFrame(tick)
  }
  function wake(to: number) {
    alpha = Math.max(alpha, to)
    if (!frame) frame = requestAnimationFrame(tick)
  }

  // --- pointer: drag a page to move (and pin) it, drag the background to pan, scroll to zoom
  function toWorld(clientX: number, clientY: number) {
    const m = root.getScreenCTM()
    if (!m) return { x: 0, y: 0 }
    const p = new DOMPoint(clientX, clientY).matrixTransform(m.inverse())
    return { x: p.x, y: p.y }
  }
  let dragging: { node: DrawnNode | null; x: number; y: number; moved: boolean; id: number } | null =
    null
  let lastClick = { slug: '', at: 0 }
  function onDown(e: PointerEvent) {
    if (e.button !== 0) return
    const target = (e.target as Element).closest('.wg-node')
    const node = target ? nodeBySlug.get(target.getAttribute('data-slug')!) ?? null : null
    if (node) {
      const p = toWorld(e.clientX, e.clientY)
      dragging = { node, x: p.x - node.x, y: p.y - node.y, moved: false, id: e.pointerId }
    } else {
      dragging = { node: null, x: e.clientX, y: e.clientY, moved: false, id: e.pointerId }
      svg.classList.add('panning')
    }
    svg.setPointerCapture(e.pointerId)
  }
  function onMove(e: PointerEvent) {
    if (!dragging) {
      const target = (e.target as Element).closest('.wg-node')
      const slug = target?.getAttribute('data-slug') ?? null
      if (slug !== hover) {
        hover = slug
        highlight()
      }
      return
    }
    if (dragging.node) {
      const p = toWorld(e.clientX, e.clientY)
      const n = dragging.node
      if (Math.hypot(p.x - dragging.x - n.x, p.y - dragging.y - n.y) > 2) dragging.moved = true
      if (!dragging.moved) return
      n.pinned = true
      n.x = p.x - dragging.x
      n.y = p.y - dragging.y
      n.vx = n.vy = 0
      n.g.classList.add('pinned')
      placeNode(n)
      for (const edge of edgesOf.get(n.slug) ?? []) placeEdge(edge)
      wake(0.25)
    } else {
      const m = svg.getScreenCTM()
      const k = m ? m.a : 1
      const dx = (e.clientX - dragging.x) / k
      const dy = (e.clientY - dragging.y) / k
      if (Math.abs(dx) + Math.abs(dy) > 2) dragging.moved = true
      dragging.x = e.clientX
      dragging.y = e.clientY
      tx += dx
      ty += dy
      apply()
    }
  }
  function onUp(e: PointerEvent) {
    const d = dragging
    dragging = null
    svg.classList.remove('panning')
    if (svg.hasPointerCapture(e.pointerId)) svg.releasePointerCapture(e.pointerId)
    if (!d || d.moved) return
    if (d.node) {
      const now = performance.now()
      if (lastClick.slug === d.node.slug && now - lastClick.at < 350) {
        on.onOpen(d.node.slug) // a double click opens the page
        lastClick = { slug: '', at: 0 }
        return
      }
      lastClick = { slug: d.node.slug, at: now }
      select(d.node.slug)
    } else {
      select(null)
      if (focused) focusGroup(null)
    }
  }
  function onWheel(e: WheelEvent) {
    e.preventDefault()
    const m = svg.getScreenCTM()
    if (!m) return
    const p = new DOMPoint(e.clientX, e.clientY).matrixTransform(m.inverse())
    const next = Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale * Math.exp(-e.deltaY * 0.0015)))
    tx = p.x - ((p.x - tx) * next) / scale
    ty = p.y - ((p.y - ty) * next) / scale
    scale = next
    cancelAnimationFrame(flight)
    apply()
  }
  function onLeave() {
    if (hover) {
      hover = null
      highlight()
    }
  }
  function onKey(e: KeyboardEvent) {
    if (e.key === 'Escape') {
      select(null)
      if (focused) focusGroup(null)
    }
  }
  svg.addEventListener('pointerdown', onDown)
  svg.addEventListener('pointermove', onMove)
  svg.addEventListener('pointerup', onUp)
  svg.addEventListener('pointercancel', onUp)
  svg.addEventListener('pointerleave', onLeave)
  svg.addEventListener('wheel', onWheel, { passive: false })
  window.addEventListener('keydown', onKey)
  const resize = new ResizeObserver(() => {
    const { w, h } = size()
    svg.setAttribute('viewBox', `0 0 ${w} ${h}`)
  })
  resize.observe(container)

  function select(slug: string | null) {
    selected = slug
    on.onSelect(slug)
    highlight()
  }

  // --- start at a readable scale, centred on the biggest group
  const start = initialGraphViewport(nodes, width, height, layout.clusters[0])
  scale = start.scale
  tx = start.x
  ty = start.y
  placeAll()
  apply()
  refreshLabels()
  if (nodes.length) frame = requestAnimationFrame(tick)

  const api: GraphApi = {
    fit: () => frameNodes(nodes, 1),
    zoom: (factor) => {
      const { w, h } = size()
      const next = Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale * factor))
      flyTo(w / 2 - ((w / 2 - tx) * next) / scale, h / 2 - ((h / 2 - ty) * next) / scale, next, 250)
    },
    show: (slug) => {
      const n = nodeBySlug.get(slug)
      if (!n) return
      const { w, h } = size()
      const s = Math.max(scale, 1)
      flyTo(w / 2 - n.x * s, h / 2 - n.y * s, s)
      select(slug)
    },
    focusGroup,
    setMatches: (slugs) => {
      matches = slugs
      highlight()
    },
    select,
    dispose: () => {
      cancelAnimationFrame(frame)
      cancelAnimationFrame(flight)
      cancelAnimationFrame(labelsQueued)
      window.removeEventListener('keydown', onKey)
      resize.disconnect()
      container.replaceChildren()
    },
  }
  return { api, groups }
}

export default function GraphTab({ build, onOpenPage }: Props) {
  const [graph, setGraph] = useState<Graph | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [hidden, setHidden] = useState<Set<string>>(new Set())
  const [hideLonely, setHideLonely] = useState(false)
  const [search, setSearch] = useState('')
  const [chosen, setChosen] = useState<string | null>(null)
  const [groups, setGroups] = useState<Group[]>([])
  const [focused, setFocused] = useState<string | null>(null)
  const [reloads, setReloads] = useState(0)
  const stageRef = useRef<HTMLDivElement>(null)
  const apiRef = useRef<GraphApi | null>(null)
  const openRef = useRef(onOpenPage)
  openRef.current = onOpenPage

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

  const shown = useMemo(() => {
    if (!graph) return null
    let pages = graph.pages.filter((p) => !hidden.has(p.page_type))
    if (hideLonely) pages = pages.filter((p) => p.n_in + p.n_out > 0)
    const slugs = new Set(pages.map((p) => p.slug))
    const links = graph.links.filter((l) => slugs.has(l.source) && slugs.has(l.target))
    return { pages, links, bySlug: new Map(pages.map((p) => [p.slug, p])) }
  }, [graph, hidden, hideLonely])

  useEffect(() => {
    const stage = stageRef.current
    if (!stage || !shown || !shown.pages.length) return
    const drawn = drawGraph(stage, shown.pages, shown.links, {
      onSelect: setChosen,
      onOpen: (slug) => openRef.current(slug),
      onFocus: setFocused,
    })
    apiRef.current = drawn.api
    setGroups(drawn.groups)
    setChosen(null)
    setFocused(null)
    return () => {
      drawn.api.dispose()
      apiRef.current = null
    }
  }, [shown])

  const query = search.trim().toLowerCase()
  const matches = useMemo(() => {
    if (!shown || !query) return null
    return shown.pages.filter((p) => p.title.toLowerCase().includes(query))
  }, [shown, query])
  useEffect(() => {
    apiRef.current?.setMatches(matches ? new Set(matches.map((p) => p.slug)) : null)
  }, [matches, shown])

  const chosenPage = chosen ? shown?.bySlug.get(chosen) : undefined

  if (!build) return <div className="page muted">No wiki chosen yet.</div>
  if (error) return <div className="page error">Couldn’t load the graph: {error}</div>
  if (!graph || !shown) return <div className="page muted">Loading the graph…</div>
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
          onKeyDown={(e) => {
            if (e.key === 'Enter' && matches?.length) apiRef.current?.show(matches[0].slug)
          }}
          aria-label="Find a page"
        />
        {matches && (
          <p className="muted small">
            {matches.length === 0
              ? 'No page matches.'
              : `${matches.length} match${matches.length > 1 ? 'es' : ''}. Press Enter to go to the first.`}
          </p>
        )}
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
        {groups.length > 0 && (
          <div className="wg-groups">
            <p className="small">
              <strong>Groups</strong> <span className="muted">(click to zoom in)</span>
            </p>
            <ul>
              {groups.map((g) => (
                <li key={g.key}>
                  <button
                    className={g.key === focused ? 'link-button small on' : 'link-button small'}
                    onClick={() => apiRef.current?.focusGroup(g.key === focused ? null : g.key)}
                  >
                    {shorten(g.name, 34)}
                  </button>{' '}
                  <span className="muted small">{g.count}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
        <p className="muted small">
          {shown.pages.length} pages · {shown.links.length} links
          {graph.broken_links > 0 && ` · ${graph.broken_links} links to missing pages`}. Scroll to
          zoom, drag the background to move, drag a page to pin it, click a page to see it,
          double-click to read it.
        </p>
        {(build.status === 'building' || build.status === 'uploading') && (
          <button className="link-button small" onClick={() => setReloads((n) => n + 1)}>
            Reload the graph (the wiki is still growing)
          </button>
        )}
      </div>
      <div className="graph-stage">
        {shown.pages.length === 0 ? (
          <p className="page muted">Every page is hidden by the filters.</p>
        ) : (
          <div ref={stageRef} className="wg-canvas" />
        )}
        <div className="wg-controls">
          <button onClick={() => apiRef.current?.zoom(1.3)} aria-label="Zoom in" title="Zoom in">
            +
          </button>
          <button onClick={() => apiRef.current?.zoom(1 / 1.3)} aria-label="Zoom out" title="Zoom out">
            −
          </button>
          <button onClick={() => apiRef.current?.fit()} title="Show the whole graph">
            Fit
          </button>
        </div>
        {chosenPage && (
          <aside className="graph-card">
            <p className="small" style={{ color: typeColour(chosenPage.page_type) }}>
              {chosenPage.page_type}
            </p>
            <h3>{chosenPage.title}</h3>
            {chosenPage.summary && <p>{chosenPage.summary}</p>}
            <p className="muted small">
              {chosenPage.n_out} links out · {chosenPage.n_in} links in
              {chosenPage.sources.length > 0 &&
                ` · from ${chosenPage.sources.length} review${chosenPage.sources.length > 1 ? 's' : ''}`}
            </p>
            <button className="button" onClick={() => onOpenPage(chosenPage.slug)}>
              Read the page
            </button>
          </aside>
        )}
      </div>
    </div>
  )
}
