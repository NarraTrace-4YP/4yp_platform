// Fix (25 Nov 2026, problem review "O2 knowledge graph is hard to read"):
//   Problem: the 3D knowledge graph was a cloud of same-coloured balls. Colours were per
//   review, so a single review (or a few) came out in one or two colours; nothing was
//   named, relationships all looked alike, and pointing at a node did nothing.
//   Why: nothing grouped the entities or showed the relationship types, and the view only
//   reacted to clicks.
//   Solution: like WeKnora's wiki graph, but in 3D. Entities are coloured by theme, the
//   groups that WeKnora's community finder (graphCommunities in wikiGraphLayout.ts) picks
//   out of the links, each named after its best-connected entity; the review colouring is
//   still a choice. Relationships are coloured by type, with a legend. The best-connected
//   entities carry name labels. Pointing at an entity lights it, its neighbours and their
//   relationships (with moving particles showing direction) and fades the rest; clicking
//   flies the camera to it; clicking a theme frames the whole theme.
// Fix (25 Nov 2026, problem review "O2 knowledge graph split by review"):
//   Problem: every review's entities were separate, so the graph was one island per review.
//   Solution: get_kgraph joins named organisations across reviews and links each agency to a
//   type node (f/dhr/O2/entities.py). Here: a "Join reviews" switch (on by default), shared
//   entities and type nodes drawn grey when colouring by review and named as such in the
//   tooltip and card, and type nodes drawn as larger, paler spheres.
import React, { useEffect, useMemo, useRef, useState } from 'react'
import ForceGraph3D, { ForceGraph3DInstance } from '3d-force-graph'
import { CanvasTexture, LinearFilter, Sprite, SpriteMaterial } from 'three'
import { backend } from './wmill'
import { Build, KnowledgeGraph, KgNode, errorMessage } from './shared'
import { graphCommunities } from './wikiGraphLayout'

type Props = { build: Build | null }
type ColourBy = 'theme' | 'review'
type DrawNode = KgNode & {
  theme: number
  colour: string
  x?: number
  y?: number
  z?: number
}
type DrawLink = { source: string | DrawNode; target: string | DrawNode; type: string }
type Theme = {
  index: number
  name: string
  review: string
  size: number
  colour: string
  members: string[]
}

const PALETTE = [
  '#0b5e8a',
  '#d1495b',
  '#2a9d8f',
  '#e9a03b',
  '#7b5ea7',
  '#4f772d',
  '#c2185b',
  '#3d5afe',
  '#8a5d00',
  '#00897b',
  '#ef6c00',
  '#5e35b1',
  '#6d4c41',
  '#1976d2',
  '#9e9d24',
  '#ad1457',
]
const RELATION_PALETTE = [
  '#5c6bc0',
  '#26a69a',
  '#ef5350',
  '#ffa726',
  '#8d6e63',
  '#ab47bc',
  '#66bb6a',
  '#29b6f6',
  '#ec407a',
  '#9ccc65',
  '#ff7043',
  '#78909c',
  '#d4e157',
  '#7e57c2',
]
const SMALL_THEME = '#a9a4a1' // themes of one or two entities
const BACKGROUND = '#fbfafa'
const FADED_NODE = '#e4e0de'
const FADED_LINK = '#ece9e7'
const LABELS = 40 // entities named in the picture (the best-connected)
const SHARED = '#6b6562' // colouring by review: an entity in two or more reviews
const TYPE_NODE = '#b9b3b0' // colouring by review: an organisation type

function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`)
}
function endId(end: string | DrawNode): string {
  return typeof end === 'string' ? end : end.id
}
function shortReview(docId: string): string {
  return docId.replace(/ - DHR$/, '').replace(/_a$/, '')
}
/** Where an entity comes from: its review, "Shared by N reviews", or "Type of organisation". */
function where(n: KgNode): string {
  return n.is_type || (n.docs?.length ?? 1) > 1 ? n.doc_id : shortReview(n.doc_id)
}
function shorten(text: string, n: number): string {
  return text.length > n ? text.slice(0, n - 1) + '…' : text
}

/** A name label that always faces the camera, drawn on a canvas. */
function textSprite(text: string, colour: string, height: number): Sprite {
  const font = 600
  const px = 48
  const canvas = document.createElement('canvas')
  const ctx = canvas.getContext('2d')!
  ctx.font = `${font} ${px}px Archivo, system-ui, sans-serif`
  const w = Math.ceil(ctx.measureText(text).width) + 24
  canvas.width = w
  canvas.height = px + 20
  ctx.font = `${font} ${px}px Archivo, system-ui, sans-serif`
  ctx.textBaseline = 'middle'
  ctx.lineJoin = 'round'
  ctx.lineWidth = 10
  ctx.strokeStyle = BACKGROUND
  ctx.strokeText(text, 12, canvas.height / 2)
  ctx.fillStyle = colour
  ctx.fillText(text, 12, canvas.height / 2)
  const texture = new CanvasTexture(canvas)
  texture.minFilter = LinearFilter
  const sprite = new Sprite(new SpriteMaterial({ map: texture, depthWrite: false, transparent: true }))
  sprite.scale.set((height * w) / canvas.height, height, 1)
  sprite.renderOrder = 10
  return sprite
}

export default function KnowledgeGraphTab({ build }: Props) {
  const [data, setData] = useState<KnowledgeGraph | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [review, setReview] = useState('')
  const [maxNodes, setMaxNodes] = useState(1500)
  const [search, setSearch] = useState('')
  const [hiddenTypes, setHiddenTypes] = useState<Set<string>>(new Set())
  const [colourBy, setColourBy] = useState<ColourBy>('theme')
  const [showLabels, setShowLabels] = useState(true)
  const [chosen, setChosen] = useState<string | null>(null)
  const [hover, setHover] = useState<string | null>(null)
  const [theme, setTheme] = useState<number | null>(null)
  const [reloads, setReloads] = useState(0)
  const [join, setJoin] = useState(true)
  const stageRef = useRef<HTMLDivElement>(null)
  const graphRef = useRef<ForceGraph3DInstance | null>(null)

  const graphOn = !!build?.settings.knowledge_base?.knowledge_graph

  useEffect(() => {
    setData(null)
    setError(null)
    setChosen(null)
    setTheme(null)
    if (!build || !graphOn) return
    let current = true
    backend
      .kgraph({ instance_id: build.instance_id, doc_id: review, max_nodes: maxNodes, join_reviews: join })
      .then((g: KnowledgeGraph) => current && setData(g))
      .catch((e: unknown) => current && setError(errorMessage(e)))
    return () => {
      current = false
    }
  }, [build?.instance_id, graphOn, review, maxNodes, reloads, join])

  const relationColour = useMemo(() => {
    const map = new Map<string, string>()
    Object.keys(data?.relation_types ?? {})
      .sort()
      .forEach((t, i) => map.set(t, RELATION_PALETTE[i % RELATION_PALETTE.length]))
    return (t: string) => map.get(t) ?? '#9e9e9e'
  }, [data])

  // Themes: WeKnora's community finder over the relationships, biggest first.
  const analysed = useMemo(() => {
    if (!data) return null
    const index = new Map(data.nodes.map((n, i) => [n.id, i]))
    const neighbours = data.nodes.map(() => [] as number[])
    for (const l of data.links) {
      const s = index.get(l.source)
      const t = index.get(l.target)
      if (s === undefined || t === undefined || s === t) continue
      neighbours[s].push(t)
      neighbours[t].push(s)
    }
    const membership = graphCommunities(neighbours.map((list) => [...new Set(list)].sort((a, b) => a - b)))
    const groups = new Map<number, number[]>()
    membership.forEach((c, i) => {
      if (!groups.has(c)) groups.set(c, [])
      groups.get(c)!.push(i)
    })
    const ranked = [...groups.values()].sort((a, b) => b.length - a.length)
    const themeOf = new Array<number>(data.nodes.length)
    const themes: Theme[] = ranked.map((members, rank) => {
      members.forEach((i) => (themeOf[i] = rank))
      const head = members.reduce((a, b) =>
        !data.nodes[b].is_type && (data.nodes[a].is_type || neighbours[b].length > neighbours[a].length) ? b : a
      )
      return {
        index: rank,
        name: data.nodes[head].name,
        review: data.nodes[head].doc_id,
        size: members.length,
        colour: members.length >= 3 ? PALETTE[rank % PALETTE.length] : SMALL_THEME,
        members: members.map((i) => data.nodes[i].id),
      }
    })
    // The same name in two reviews (Adult A, Police...) says which review each is from.
    const seen = new Map<string, number>()
    for (const t of themes) seen.set(t.name, (seen.get(t.name) ?? 0) + 1)
    for (const t of themes) if (seen.get(t.name)! > 1) t.name = `${t.name} (${shortReview(t.review)})`
    const reviewColour = new Map<string, string>()
    data.reviews.forEach((r, i) => reviewColour.set(r.doc_id, PALETTE[i % PALETTE.length]))
    return { themes, themeOf, reviewColour }
  }, [data])

  // What is drawn: the entities, and the relationships whose type is shown.
  const drawn = useMemo(() => {
    if (!data || !analysed) return null
    const nodes: DrawNode[] = data.nodes.map((n, i) => ({
      ...n,
      theme: analysed.themeOf[i],
      colour: '',
    }))
    const links: DrawLink[] = data.links
      .filter((l) => !hiddenTypes.has(l.type))
      .map((l) => ({ source: l.source, target: l.target, type: l.type }))
    const byId = new Map(nodes.map((n) => [n.id, n]))
    const neighbours = new Map<string, { node: string; type: string; out: boolean }[]>()
    for (const l of links) {
      const s = endId(l.source)
      const t = endId(l.target)
      if (!neighbours.has(s)) neighbours.set(s, [])
      if (!neighbours.has(t)) neighbours.set(t, [])
      neighbours.get(s)!.push({ node: t, type: l.type, out: true })
      neighbours.get(t)!.push({ node: s, type: l.type, out: false })
    }
    const named = new Set(
      [...nodes]
        .sort((a, b) => b.degree - a.degree)
        .slice(0, LABELS)
        .map((n) => n.id)
    )
    return { nodes, links, byId, neighbours, named }
  }, [data, analysed, hiddenTypes])

  // Colours follow the choice without a new layout.
  if (drawn && analysed) {
    for (const n of drawn.nodes) {
      n.colour =
        colourBy === 'theme'
          ? analysed.themes[n.theme].colour
          : n.is_type
            ? TYPE_NODE
            : (n.docs?.length ?? 1) > 1
              ? SHARED
              : analysed.reviewColour.get(n.doc_id) ?? SMALL_THEME
    }
  }

  const query = search.trim().toLowerCase()
  const lit = useMemo(() => {
    if (!drawn) return null
    if (query)
      return new Set(drawn.nodes.filter((n) => n.name.toLowerCase().includes(query)).map((n) => n.id))
    const centre = hover ?? chosen
    if (centre) return new Set([centre, ...(drawn.neighbours.get(centre) ?? []).map((x) => x.node)])
    if (theme !== null && analysed) return new Set(analysed.themes[theme].members)
    return null
  }, [drawn, query, hover, chosen, theme, analysed])

  // The 3D view: made once, kept while the tab is open.
  useEffect(() => {
    const el = stageRef.current
    if (!el || !graphOn) return
    const graph = new ForceGraph3D(el, { controlType: 'orbit' })
      .backgroundColor(BACKGROUND)
      .showNavInfo(false)
      .nodeId('id')
      .nodeRelSize(4)
      .nodeVal((n) => ((n as DrawNode).is_type ? 3 : 1) + Math.sqrt((n as DrawNode).degree || 0) * 1.6)
      .nodeOpacity(0.95)
      .nodeResolution(16)
      .linkOpacity(0.55)
      .linkDirectionalArrowLength(3.5)
      .linkDirectionalArrowRelPos(1)
      .linkDirectionalParticleWidth(1.6)
      .linkDirectionalParticleSpeed(0.006)
      .linkLabel((l) => escapeHtml((l as unknown as DrawLink).type))
      .nodeLabel((n) => {
        const d = n as DrawNode
        return `<div class="kg-tip"><b>${escapeHtml(d.name)}</b><br>${escapeHtml(where(d))} · ${d.degree} relationship${d.degree === 1 ? '' : 's'}</div>`
      })
      .onNodeHover((n) => {
        el.style.cursor = n ? 'pointer' : ''
        setHover(n ? (n as DrawNode).id : null)
      })
      .onNodeClick((n) => flyTo((n as DrawNode).id))
      .onBackgroundClick(() => {
        setChosen(null)
        setTheme(null)
      })
      .warmupTicks(80)
      .cooldownTicks(250)
    // Spread the groups a little more than the defaults, so themes read as separate clumps.
    const charge = graph.d3Force('charge') as unknown as { strength?: (s: number) => void } | undefined
    charge?.strength?.(-60)
    graphRef.current = graph
    const fit = () => graph.width(el.clientWidth).height(el.clientHeight)
    fit()
    const observer = new ResizeObserver(fit)
    observer.observe(el)
    return () => {
      observer.disconnect()
      graph._destructor()
      graphRef.current = null
      el.replaceChildren()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graphOn])

  // New data: a new layout, then fit it in view once it settles.
  useEffect(() => {
    const graph = graphRef.current
    if (!graph || !drawn) return
    graph.graphData({ nodes: drawn.nodes, links: drawn.links as never[] })
    let fitted = false
    graph.onEngineStop(() => {
      if (!fitted) graph.zoomToFit(600, 40)
      fitted = true
    })
  }, [drawn])

  // Name labels: the best-connected entities, and whichever is pointed at or chosen.
  useEffect(() => {
    const graph = graphRef.current
    if (!graph || !drawn) return
    graph
      .nodeThreeObjectExtend(true)
      .nodeThreeObject((n) => {
        const d = n as DrawNode
        const always = showLabels && drawn.named.has(d.id)
        if (!always && d.id !== chosen && !(query && lit?.has(d.id))) return null as never
        const size = 9 + Math.sqrt(d.degree || 0) * 1.5
        const sprite = textSprite(shorten(d.name, 30), '#201e1d', size)
        const r = 4 * Math.cbrt(1 + Math.sqrt(d.degree || 0) * 1.6)
        sprite.position.set(0, r + size * 0.7, 0)
        return sprite as never
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [drawn, showLabels, chosen, query && lit])

  // Colours and highlighting: what is lit keeps its colour; the rest fades back.
  useEffect(() => {
    const graph = graphRef.current
    if (!graph || !drawn) return
    const centre = hover ?? chosen
    const touches = (l: DrawLink) =>
      !!centre && (endId(l.source) === centre || endId(l.target) === centre)
    graph
      .nodeColor((n) => {
        const d = n as DrawNode
        return !lit || lit.has(d.id) ? d.colour : FADED_NODE
      })
      .linkColor((l) => {
        const link = l as unknown as DrawLink
        if (centre) return touches(link) ? relationColour(link.type) : FADED_LINK
        if (lit) return lit.has(endId(link.source)) && lit.has(endId(link.target)) ? relationColour(link.type) : FADED_LINK
        return relationColour(link.type)
      })
      .linkWidth((l) => (touches(l as unknown as DrawLink) ? 1.4 : 0))
      .linkDirectionalArrowColor((l) => {
        const link = l as unknown as DrawLink
        return !centre || touches(link) ? relationColour(link.type) : FADED_LINK
      })
      .linkDirectionalParticles((l) => (touches(l as unknown as DrawLink) ? 3 : 0))
      .linkDirectionalParticleColor((l) => relationColour((l as unknown as DrawLink).type))
  }, [lit, hover, chosen, drawn, colourBy, relationColour])

  function flyTo(id: string) {
    setChosen(id)
    setTheme(null)
    const graph = graphRef.current
    const node = drawn?.byId.get(id)
    if (!graph || node?.x === undefined) return
    const distance = 110
    const length = Math.hypot(node.x, node.y ?? 0, node.z ?? 0) || 1
    const ratio = 1 + distance / length
    graph.cameraPosition(
      { x: node.x * ratio, y: (node.y ?? 0) * ratio, z: (node.z ?? 0) * ratio },
      { x: node.x, y: node.y ?? 0, z: node.z ?? 0 },
      900
    )
  }

  function frameTheme(index: number | null) {
    setTheme(index)
    setChosen(null)
    const graph = graphRef.current
    if (!graph || !analysed) return
    if (index === null) {
      graph.zoomToFit(700, 40)
      return
    }
    const members = new Set(analysed.themes[index].members)
    graph.zoomToFit(800, 60, (n) => members.has((n as DrawNode).id))
  }

  if (!build) return <div className="page muted">No wiki chosen yet.</div>
  if (!graphOn)
    return (
      <div className="page">
        <h2>No knowledge graph</h2>
        <p>
          This build was made without WeKnora’s knowledge graph. Builds with{' '}
          <code>knowledge_graph</code> on (the default since 25 November 2026) have one.
        </p>
      </div>
    )
  if (error) return <div className="page error">Couldn’t load the knowledge graph: {error}</div>

  const chosenNode = chosen ? drawn?.byId.get(chosen) : undefined
  const chosenLinks = chosen ? drawn?.neighbours.get(chosen) ?? [] : []
  const types = Object.entries(data?.relation_types ?? {}).sort((a, b) => b[1] - a[1])
  const themes = (analysed?.themes ?? []).filter((t) => t.size >= 3).slice(0, 16)
  const matches = query && lit ? lit.size : null

  return (
    <div className="graph-view">
      <div className="graph-tools">
        <label className="small">
          Review
          <select value={review} onChange={(e) => setReview(e.target.value)}>
            <option value="">Every review</option>
            {(data?.reviews ?? []).map((r) => (
              <option key={r.doc_id} value={r.doc_id}>
                {shortReview(r.doc_id)} ({r.n_nodes})
              </option>
            ))}
          </select>
        </label>
        <input
          type="search"
          placeholder="Find a person, agency or event…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          onKeyDown={(e) => {
            if (e.key !== 'Enter' || !drawn || !query) return
            const first = drawn.nodes.find((n) => n.name.toLowerCase().includes(query))
            if (first) flyTo(first.id)
          }}
          aria-label="Find an entity"
        />
        {matches !== null && (
          <p className="muted small">
            {matches === 0
              ? 'Nothing matches.'
              : `${matches} match${matches > 1 ? 'es' : ''}. Press Enter to go to the first.`}
          </p>
        )}
        <label className="small" title="Named organisations become one entity across reviews; agencies link to a node for their type">
          <input type="checkbox" checked={join} onChange={(e) => setJoin(e.target.checked)} /> Join
          reviews (named organisations shared, grouped by type)
        </label>
        <div className="kg-switch small" role="radiogroup" aria-label="Colour entities by">
          Colour by{' '}
          {(['theme', 'review'] as const).map((c) => (
            <button
              key={c}
              role="radio"
              aria-checked={colourBy === c}
              className={colourBy === c ? 'on' : ''}
              onClick={() => setColourBy(c)}
            >
              {c}
            </button>
          ))}
        </div>
        {colourBy === 'theme' && themes.length > 0 && (
          <div>
            <p className="rail-group-name">
              Themes <span className="muted">(click to frame)</span>
            </p>
            <ul className="legend kg-legend">
              {themes.map((t) => (
                <li key={t.index}>
                  <button
                    className={t.index === theme ? 'link-button small on' : 'link-button small'}
                    onClick={() => frameTheme(t.index === theme ? null : t.index)}
                  >
                    <span className="swatch" style={{ background: t.colour }} />{' '}
                    {shorten(t.name, 28)}
                  </button>{' '}
                  <span className="muted small">{t.size}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
        {colourBy === 'review' && (
          <ul className="legend kg-legend">
            {data?.joined && (
              <>
                <li className="small">
                  <span className="swatch" style={{ background: SHARED }} /> In two or more reviews{' '}
                  <span className="muted">{data.shared_entities}</span>
                </li>
                <li className="small">
                  <span className="swatch" style={{ background: TYPE_NODE }} /> Type of organisation{' '}
                  <span className="muted">{data.organisation_types}</span>
                </li>
              </>
            )}
            {(data?.reviews ?? []).slice(0, 16).map((r) => (
              <li key={r.doc_id} className="small">
                <span className="swatch" style={{ background: analysed?.reviewColour.get(r.doc_id) }} />{' '}
                {shortReview(r.doc_id)} <span className="muted">{r.n_nodes}</span>
              </li>
            ))}
          </ul>
        )}
        {types.length > 0 && (
          <div>
            <p className="rail-group-name">Relationships</p>
            <ul className="legend">
              {types.map(([t, n]) => (
                <li key={t}>
                  <label className="small">
                    <input
                      type="checkbox"
                      checked={!hiddenTypes.has(t)}
                      onChange={() =>
                        setHiddenTypes((h) => {
                          const next = new Set(h)
                          if (next.has(t)) next.delete(t)
                          else next.add(t)
                          return next
                        })
                      }
                    />{' '}
                    <span className="kg-line" style={{ background: relationColour(t) }} /> {t}{' '}
                    <span className="muted">{n}</span>
                  </label>
                </li>
              ))}
            </ul>
          </div>
        )}
        <label className="small">
          <input type="checkbox" checked={showLabels} onChange={(e) => setShowLabels(e.target.checked)} />{' '}
          Name the {LABELS} best-connected entities
        </label>
        {data && (
          <p className="muted small">
            {data.nodes.length.toLocaleString()} entities
            {data.truncated && ` (the best-connected of ${data.total_nodes.toLocaleString()})`} ·{' '}
            {drawn?.links.length.toLocaleString()} relationships. Point at an entity to light up
            its relationships, click to fly to it. Drag to turn, scroll to zoom, right-drag to
            move.
          </p>
        )}
        {data?.truncated && (
          <label className="small">
            Entities shown{' '}
            <select value={maxNodes} onChange={(e) => setMaxNodes(Number(e.target.value))}>
              {[500, 1500, 3000, 5000].map((n) => (
                <option key={n} value={n}>
                  {n.toLocaleString()}
                </option>
              ))}
            </select>
          </label>
        )}
        {(build.status === 'building' || build.status === 'uploading') && (
          <button className="link-button small" onClick={() => setReloads((n) => n + 1)}>
            Reload (the graph is still growing)
          </button>
        )}
      </div>
      <div className="graph-stage">
        <div ref={stageRef} className="kg-canvas" />
        {!data && <p className="kg-overlay muted">Loading the knowledge graph…</p>}
        {data && !data.nodes.length && (
          <p className="kg-overlay">
            No entities yet. The knowledge graph is copied from WeKnora at each check of the build
            (it needs the server’s graph database and <code>f/dhr/weknora_graph_password</code>).
          </p>
        )}
        <div className="wg-controls">
          <button onClick={() => frameTheme(null)} title="Show the whole graph">
            Fit
          </button>
        </div>
        {chosenNode && (
          <aside className="graph-card">
            <p className="small" style={{ color: chosenNode.colour }}>
              {where(chosenNode)}
              {analysed && ` · theme: ${shorten(analysed.themes[chosenNode.theme].name, 30)}`}
            </p>
            <h3>{chosenNode.name}</h3>
            {!chosenNode.is_type && (chosenNode.docs?.length ?? 1) > 1 && (
              <p className="small muted">
                In: {chosenNode.docs.map(shortReview).join('; ')}
              </p>
            )}
            {chosenNode.attributes.length > 0 && (
              <ul className="kg-attributes">
                {chosenNode.attributes.slice(0, 8).map((a) => (
                  <li key={a}>{a}</li>
                ))}
              </ul>
            )}
            {chosenLinks.length > 0 && (
              <ul className="kg-relations small">
                {chosenLinks.slice(0, 20).map((r, i) => (
                  <li key={i}>
                    <span className="kg-line" style={{ background: relationColour(r.type) }} />{' '}
                    {r.out ? `${r.type} → ` : `← ${r.type} `}
                    <button className="link-button" onClick={() => flyTo(r.node)}>
                      {drawn?.byId.get(r.node)?.name ?? r.node}
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <p className="muted small">
              Found in {chosenNode.n_chunks} chunk{chosenNode.n_chunks === 1 ? '' : 's'}
            </p>
          </aside>
        )}
      </div>
    </div>
  )
}
