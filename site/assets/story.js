// The reading scene on the project page, drawn with three.js. Scroll position chooses
// the scene; each chapter in the HTML has a data-scene number:
//   0  a pile of reviews
//   1  one review, brought forward (one review for each death)
//   2  all 100 reviews laid out (patterns only show across many)
//   3  threads joining highlighted passages in different reviews (two ways to question)
//   4  the threads gathered into linked wiki pages (a wiki of agencies, events ...)
// Everything the scene shows is also said in the chapter text, so the canvas is hidden
// from screen readers and the page reads the same without WebGL.
import {
  CanvasTexture,
  Color,
  Euler,
  Fog,
  Group,
  MathUtils,
  Mesh,
  MeshBasicMaterial,
  PerspectiveCamera,
  PlaneGeometry,
  QuadraticBezierCurve3,
  Quaternion,
  SRGBColorSpace,
  Scene,
  Vector3,
  WebGLRenderer,
  Line2,
  LineGeometry,
  LineMaterial,
} from './vendor/three.min.js'

const BRAND = {
  ground: '#f3f2f2',
  paper: '#ffffff',
  bar: '#c9c5c3',
  barDark: '#a9a4a2',
  wash: '#daeaf3',
  blue: '#0b5e8a',
  blueDeep: '#08425f',
}
const PAGE_W = 0.707 // A4 proportions
const PAGE_H = 1
const N_PAGES = 100
const N_VARIANTS = 12
const WIKI_PAGES = ['Person', 'Agency', 'Relationship', 'Event', 'Recommendation']

const stage = document.querySelector('.story-stage')
const canvas = stage?.querySelector('.story-canvas')
const story = document.querySelector('.story')

// Phones get the chapters as an ordinary article (site.css), so the scene only starts on a
// wide screen, including one that becomes wide later.
const wide = window.matchMedia('(min-width: 861px)')
if (canvas && story) {
  if (wide.matches) start()
  else
    wide.addEventListener('change', function once(event) {
      if (!event.matches) return
      wide.removeEventListener('change', once)
      start()
    })
}

function start() {
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  const finePointer = window.matchMedia('(pointer: fine)').matches

  let renderer
  try {
    renderer = new WebGLRenderer({ canvas, antialias: true, powerPreference: 'high-performance' })
  } catch {
    document.documentElement.classList.add('no-webgl')
    return
  }
  renderer.setClearColor(new Color(BRAND.ground), 1)
  // If the browser takes WebGL away, show the plain drawing instead of an empty canvas.
  canvas.addEventListener('webglcontextlost', (event) => {
    event.preventDefault()
    document.documentElement.classList.add('no-webgl')
  })
  canvas.addEventListener('webglcontextrestored', () => {
    document.documentElement.classList.remove('no-webgl')
    wanted = true
  })
  renderer.outputColorSpace = SRGBColorSpace
  const maxAnisotropy = renderer.capabilities.getMaxAnisotropy()

  const scene = new Scene()
  scene.fog = new Fog(new Color(BRAND.ground), 5.5, 13)
  const camera = new PerspectiveCamera(32, 1, 0.1, 60)

  // A seeded random generator: the same layout on every visit.
  let seed = 407
  const random = () => {
    seed = (seed * 16807) % 2147483647
    return (seed - 1) / 2147483646
  }
  const spread = (amount) => (random() * 2 - 1) * amount

  // ---- Textures -------------------------------------------------------------------

  // A review page: lines of text as grey bars, with one to three highlighted passages.
  // Returns the texture and where each highlight is, in page coordinates (0-1 from top left).
  function reviewTexture() {
    const w = 512
    const h = Math.round(w * (PAGE_H / PAGE_W))
    const c = document.createElement('canvas')
    c.width = w
    c.height = h
    const g = c.getContext('2d')
    g.fillStyle = BRAND.paper
    g.fillRect(0, 0, w, h)
    const left = 46
    const width = w - left * 2
    const lineH = 10
    const step = 21
    let y = 58
    g.fillStyle = BRAND.barDark
    g.fillRect(left, y, width * (0.45 + random() * 0.25), 16)
    y += 46
    const lines = []
    while (y < h - 60) {
      const n = 3 + Math.floor(random() * 5)
      for (let k = 0; k < n && y < h - 60; k += 1) {
        const last = k === n - 1
        lines.push({ y, len: last ? 0.25 + random() * 0.45 : 0.82 + random() * 0.18 })
        y += step
      }
      y += 14
    }
    const nHighlights = 1 + Math.floor(random() * 3)
    const picks = new Set()
    while (picks.size < nHighlights) picks.add(Math.floor(random() * lines.length))
    const highlights = []
    lines.forEach((line, index) => {
      const full = width * line.len
      if (!picks.has(index)) {
        g.fillStyle = BRAND.bar
        g.fillRect(left, line.y, full, lineH)
        return
      }
      const start = full * random() * 0.35
      const span = Math.max(width * 0.22, (full - start) * (0.5 + random() * 0.5))
      g.fillStyle = BRAND.bar
      g.fillRect(left, line.y, start - 6 > 0 ? start - 6 : 0, lineH)
      g.fillRect(left + start + span + 6, line.y, Math.max(0, full - start - span - 6), lineH)
      g.fillStyle = BRAND.wash
      g.fillRect(left + start, line.y - 8, span, lineH + 16)
      g.fillStyle = BRAND.blue
      g.fillRect(left + start, line.y + lineH + 6, span, 3)
      g.fillRect(left + start + 8, line.y, span - 16, lineH)
      highlights.push({ u: (left + start + span / 2) / w, v: (line.y + lineH / 2) / h })
    })
    const texture = new CanvasTexture(c)
    texture.colorSpace = SRGBColorSpace
    texture.anisotropy = maxAnisotropy
    return { texture, highlights }
  }

  // A wiki page: a blue title, a few lines and links.
  function wikiTexture() {
    const w = 384
    const h = 480
    const c = document.createElement('canvas')
    c.width = w
    c.height = h
    const g = c.getContext('2d')
    g.fillStyle = BRAND.paper
    g.fillRect(0, 0, w, h)
    g.fillStyle = BRAND.blue
    g.fillRect(0, 0, w, 14)
    g.fillStyle = BRAND.blueDeep
    g.fillRect(36, 52, w * 0.5, 18)
    let y = 104
    for (let k = 0; k < 9; k += 1) {
      const link = k % 3 === 1
      g.fillStyle = link ? BRAND.wash : BRAND.bar
      const len = (w - 72) * (k % 3 === 2 ? 0.55 : 0.92)
      g.fillRect(36, y, len, 10)
      if (link) {
        g.fillStyle = BRAND.blue
        g.fillRect(36, y + 13, len * 0.6, 3)
      }
      y += 32
    }
    const texture = new CanvasTexture(c)
    texture.colorSpace = SRGBColorSpace
    texture.anisotropy = maxAnisotropy
    return texture
  }

  // A soft shadow under each page.
  function shadowTexture() {
    const size = 128
    const c = document.createElement('canvas')
    c.width = size
    c.height = size
    const g = c.getContext('2d')
    g.filter = 'blur(10px)'
    g.fillStyle = 'rgba(32, 30, 29, 0.32)'
    g.fillRect(22, 22, size - 44, size - 44)
    const texture = new CanvasTexture(c)
    return texture
  }

  const variants = Array.from({ length: N_VARIANTS }, reviewTexture)
  const shadowMap = shadowTexture()
  const pageGeometry = new PlaneGeometry(PAGE_W, PAGE_H)
  const shadowGeometry = new PlaneGeometry(PAGE_W * 1.18, PAGE_H * 1.14)

  // ---- The reviews ----------------------------------------------------------------

  const corpus = new Group()
  scene.add(corpus)

  const pages = []
  for (let index = 0; index < N_PAGES; index += 1) {
    const variant = variants[index % N_VARIANTS]
    const material = new MeshBasicMaterial({ map: variant.texture, transparent: true })
    const mesh = new Mesh(pageGeometry, material)
    const shadow = new Mesh(
      shadowGeometry,
      new MeshBasicMaterial({ map: shadowMap, transparent: true, depthWrite: false }),
    )
    shadow.position.set(0.018, -0.028, -0.004)
    mesh.add(shadow)
    corpus.add(mesh)
    pages.push({ mesh, material, shadow, highlights: variant.highlights, layouts: [] })
  }

  // Layouts, in the corpus group's own coordinates.
  const cols = 10
  const grid = { dx: 0.37, dy: 0.5, scale: 0.42 }
  pages.forEach((page, index) => {
    const pile = {
      position: new Vector3(spread(0.02), spread(0.02), -index * 0.0065),
      rotation: new Euler(0, 0, spread(index < 8 ? 0.11 : 0.05)),
      scale: 1,
    }
    if (index > 0 && index < 7) pile.position.x += 0.03 * index * (index % 2 ? 1 : -1)
    const focus =
      index === 0
        ? { position: new Vector3(-0.05, 0.02, 1.15), rotation: new Euler(0, 0, 0), scale: 1.05 }
        : {
            position: pile.position.clone().add(new Vector3(0, 0, -0.45)),
            rotation: pile.rotation.clone(),
            scale: 1,
          }
    const col = index % cols
    const row = Math.floor(index / cols)
    const laid = {
      position: new Vector3(
        (col - (cols - 1) / 2) * grid.dx + spread(0.015),
        ((cols - 1) / 2 - row) * grid.dy + spread(0.015),
        spread(0.012),
      ),
      rotation: new Euler(0, 0, spread(0.035)),
      scale: grid.scale,
    }
    page.layouts = [pile, focus, laid, laid, laid]
    page.delay = ((col + row) / 18) * 0.4 + random() * 0.08
  })

  // Where a highlighted passage sits once the pages are laid out.
  const highlightPoint = (page, k) => {
    const h = page.highlights[k % page.highlights.length]
    const laid = page.layouts[2]
    const local = new Vector3((h.u - 0.5) * PAGE_W, (0.5 - h.v) * PAGE_H, 0.002)
    local.applyEuler(laid.rotation).multiplyScalar(laid.scale).add(laid.position)
    return local
  }

  // ---- Threads and wiki pages ----------------------------------------------------

  const lineMaterials = []
  const makeLine = (curve, segments, width, opacity) => {
    const points = curve.getPoints(segments)
    const positions = points.flatMap((p) => [p.x, p.y, p.z])
    const geometry = new LineGeometry()
    geometry.setPositions(positions)
    const material = new LineMaterial({
      color: BRAND.blue,
      linewidth: width,
      transparent: true,
      opacity,
      depthWrite: false,
    })
    lineMaterials.push(material)
    const line = new Line2(geometry, material)
    line.userData = { segments, baseOpacity: opacity }
    line.geometry.instanceCount = 0
    corpus.add(line)
    return line
  }

  const lift = (a, b, height) => {
    const mid = a.clone().add(b).multiplyScalar(0.5)
    mid.z += height
    return new QuadraticBezierCurve3(a, mid, b)
  }

  // Threads between passages in different reviews.
  const threads = []
  const distance = (i, j) =>
    Math.abs((i % cols) - (j % cols)) + Math.abs(((i / cols) | 0) - ((j / cols) | 0))
  for (let k = 0; k < 30; k += 1) {
    const a = Math.floor(random() * N_PAGES)
    let b = Math.floor(random() * N_PAGES)
    while (b === a || distance(a, b) < 2 || distance(a, b) > 7) b = Math.floor(random() * N_PAGES)
    const start = highlightPoint(pages[a], k)
    const end = highlightPoint(pages[b], k + 1)
    const line = makeLine(lift(start, end, 0.25 + start.distanceTo(end) * 0.22), 28, 1.6, 0.85)
    line.userData.delay = random() * 0.45
    threads.push(line)
  }

  // Wiki pages float above the laid-out reviews, each fed by passages from many reviews.
  const wikiMap = wikiTexture()
  const wikiGeometry = new PlaneGeometry(0.5, 0.625)
  const wikiSpots = [
    new Vector3(-0.72, 0.72, 1.15),
    new Vector3(0.62, 0.88, 1.25),
    new Vector3(0.0, -0.08, 1.45),
    new Vector3(-0.58, -0.92, 1.2),
    new Vector3(0.78, -0.78, 1.3),
  ]
  const wiki = wikiSpots.map((spot, index) => {
    const material = new MeshBasicMaterial({ map: wikiMap, transparent: true, opacity: 0 })
    const mesh = new Mesh(wikiGeometry, material)
    const shadow = new Mesh(
      shadowGeometry,
      new MeshBasicMaterial({ map: shadowMap, transparent: true, depthWrite: false, opacity: 0 }),
    )
    shadow.scale.set(0.66, 0.6, 1)
    shadow.position.set(0.018, -0.03, -0.01)
    mesh.add(shadow)
    mesh.position.copy(spot)
    corpus.add(mesh)
    const feeds = []
    // Passages come from reviews laid out near the wiki page, so the threads stay short.
    const nearby = pages.filter((page) => {
      const p = page.layouts[2].position
      return Math.abs(p.x - spot.x) < 1.1 && Math.abs(p.y - spot.y) < 1.4
    })
    for (let k = 0; k < 6; k += 1) {
      const page = nearby[Math.floor(random() * nearby.length)]
      const from = highlightPoint(page, k + index)
      const to = spot.clone().add(new Vector3(spread(0.1), -0.2, 0))
      const line = makeLine(lift(from, to, 0.18), 24, 1.3, 0.7)
      line.userData.delay = random() * 0.4
      feeds.push(line)
    }
    const label = document.createElement('span')
    label.className = 'stage-label'
    label.textContent = `${WIKI_PAGES[index]} page`
    stage.querySelector('.stage-labels')?.append(label)
    return { mesh, material, shadow, feeds, label }
  })
  const links = [
    [0, 2],
    [1, 2],
    [2, 3],
    [2, 4],
    [0, 1],
    [3, 4],
  ].map(([a, b]) => {
    const line = makeLine(lift(wikiSpots[a], wikiSpots[b], 0.12), 20, 2, 0.95)
    line.userData.delay = random() * 0.3
    return line
  })

  // ---- Scene keyframes ------------------------------------------------------------

  // The scene sits right of the chapter text on wide screens, and above it on narrow ones.
  let centre = new Vector3()
  const frames = [
    // The corpus group's position and rotation, then the camera's position and target,
    // all relative to the centre.
    { group: [0, 0.12, 0, -0.3, -0.48, 0.07], eye: [0, 0.12, 3.9], at: [0, 0.1, 0] },
    { group: [0, 0.05, 0, -0.1, -0.22, 0.03], eye: [0, 0.05, 4.0], at: [0, 0.05, 0.25] },
    { group: [0, -0.1, -0.9, -0.95, 0, 0.38], eye: [0, 0.5, 5.3], at: [0, -0.25, -0.9] },
    { group: [0, -0.05, -0.7, -0.88, 0, 0.26], eye: [0, 0.45, 4.6], at: [0, -0.15, -0.6] },
    { group: [0, -0.35, -1.3, -0.92, 0, 0.2], eye: [0, 0.95, 4.9], at: [0, 0.42, -0.4] },
  ]

  const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2)
  const hold = (t) => MathUtils.smoothstep(t, 0.22, 0.78) // linger on each scene
  const band = (s, from, to) => MathUtils.clamp((s - from) / (to - from), 0, 1)

  const groupEuler = new Euler()
  const tmpA = new Vector3()
  const tmpB = new Vector3()
  const tmpQ = new Quaternion()
  const parentQ = new Quaternion()

  function pose(s, idle) {
    const i = Math.min(Math.floor(s), frames.length - 1)
    const j = Math.min(i + 1, frames.length - 1)
    const f = ease(hold(s - i))
    const A = frames[i]
    const B = frames[j]
    const mix = (a, b) => a + (b - a) * f

    corpus.position.set(
      centre.x + mix(A.group[0], B.group[0]),
      centre.y + mix(A.group[1], B.group[1]),
      centre.z + mix(A.group[2], B.group[2]),
    )
    groupEuler.set(
      mix(A.group[3], B.group[3]),
      mix(A.group[4], B.group[4]) + idle.sway,
      mix(A.group[5], B.group[5]),
    )
    corpus.rotation.copy(groupEuler)
    tmpA.set(
      centre.x + mix(A.at[0], B.at[0]),
      centre.y + mix(A.at[1], B.at[1]),
      centre.z + mix(A.at[2], B.at[2]),
    )
    tmpB.set(
      mix(A.eye[0], B.eye[0]) - mix(A.at[0], B.at[0]),
      mix(A.eye[1], B.eye[1]) - mix(A.at[1], B.at[1]),
      mix(A.eye[2], B.eye[2]) - mix(A.at[2], B.at[2]),
    )
    camera.position.copy(tmpA).addScaledVector(tmpB, fit)
    camera.position.x += idle.x
    camera.position.y += idle.y
    camera.lookAt(tmpA)

    // Pages: dealt out from the pile in a wave, from one corner to the other.
    const dim = 1 - 0.55 * MathUtils.smoothstep(s, 3.3, 3.95)
    pages.forEach((page) => {
      const a = page.layouts[i]
      const b = page.layouts[j]
      let t = f
      if (i === 1 && j === 2) t = ease(MathUtils.clamp((hold(s - i) - page.delay) / 0.52, 0, 1))
      if (i === 0 && j === 1) t = f
      page.mesh.position.lerpVectors(a.position, b.position, t)
      page.mesh.rotation.set(
        a.rotation.x + (b.rotation.x - a.rotation.x) * t,
        a.rotation.y + (b.rotation.y - a.rotation.y) * t,
        a.rotation.z + (b.rotation.z - a.rotation.z) * t,
      )
      page.mesh.scale.setScalar(a.scale + (b.scale - a.scale) * t)
      page.material.opacity = dim
      page.shadow.material.opacity = dim
    })

    // Threads draw in during scene 3 and give way to the wiki in scene 4.
    const draw = (line, p) => {
      const t = MathUtils.clamp((p - line.userData.delay) / (1 - line.userData.delay), 0, 1)
      line.geometry.instanceCount = Math.round(line.userData.segments * t)
      line.visible = t > 0
    }
    const threadIn = band(s, 2.25, 3.0)
    const threadFade = 1 - 0.85 * MathUtils.smoothstep(s, 3.3, 3.95)
    threads.forEach((line) => {
      draw(line, threadIn)
      line.material.opacity = line.userData.baseOpacity * threadFade
    })

    const wikiIn = band(s, 3.25, 3.95)
    camera.updateMatrixWorld()
    corpus.updateMatrixWorld()
    corpus.getWorldQuaternion(parentQ).invert()
    const rect = canvas.getBoundingClientRect()
    wiki.forEach((node, index) => {
      const t = MathUtils.clamp((wikiIn - index * 0.08) / 0.6, 0, 1)
      node.material.opacity = t
      node.shadow.material.opacity = t
      node.mesh.visible = t > 0
      node.mesh.position.copy(wikiSpots[index]).add(tmpB.set(0, 0, -0.5 * (1 - ease(t))))
      // Face the camera, whatever the corpus group's tilt.
      node.mesh.quaternion.copy(parentQ).multiply(tmpQ.copy(camera.quaternion))
      node.feeds.forEach((line) => draw(line, band(s, 3.3, 3.95)))
      // Its label sits just above it on screen.
      node.mesh.getWorldPosition(tmpA)
      tmpA.y += 0.36
      tmpA.project(camera)
      const x = MathUtils.clamp((tmpA.x * 0.5 + 0.5) * rect.width, labelMin, rect.width - 90)
      const y = MathUtils.clamp((-tmpA.y * 0.5 + 0.5) * rect.height, 90, rect.height)
      node.label.style.transform = `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px) translate(-50%, -100%)`
      node.label.style.opacity = String(MathUtils.smoothstep(t, 0.55, 1))
    })
    links.forEach((line) => draw(line, band(s, 3.6, 4.0)))
  }

  // ---- Size, scroll and pointer -----------------------------------------------------

  let width = 0
  let height = 0
  let labelMin = 80
  let fit = 1 // how far the camera pulls back so a tall, narrow canvas still fits the scene
  function resize() {
    const rect = canvas.getBoundingClientRect()
    width = Math.max(1, Math.round(rect.width))
    height = Math.max(1, Math.round(rect.height))
    const dpr = Math.min(window.devicePixelRatio || 1, 2)
    renderer.setPixelRatio(dpr)
    renderer.setSize(width, height, false)
    camera.aspect = width / height
    camera.fov = 34
    camera.updateProjectionMatrix()
    fit = MathUtils.clamp(1.02 / camera.aspect, 1, 1.9)
    scene.fog.near = 5.5 * fit
    scene.fog.far = 13 * fit
    lineMaterials.forEach((material) => material.resolution.set(width, height))
    // Wide screens: the scene's centre is moved right of the chapter text by shifting the
    // camera's view, not the scene. Phones: the chapters cover the bottom of the screen,
    // so the scene is moved up instead.
    if (window.innerWidth > 860)
      camera.setViewOffset(width, height, -width * 0.23, 0, width, height)
    else camera.setViewOffset(width, height, 0, height * 0.16, width, height)
    centre = new Vector3()
    // Labels never go left of the text column's edge (--text-right in site.css).
    labelMin = window.innerWidth > 860 ? Math.max(560, window.innerWidth * 0.42) + 90 : 80
    wanted = true
  }

  const chapters = [...document.querySelectorAll('.story-chapters [data-scene]')]
  function sceneFromScroll() {
    const middle = window.innerHeight / 2
    const centres = chapters.map((chapter) => {
      const rect = chapter.getBoundingClientRect()
      return rect.top + rect.height / 2
    })
    if (middle <= centres[0]) return 0
    for (let k = 0; k < centres.length - 1; k += 1) {
      if (middle < centres[k + 1]) return k + (middle - centres[k]) / (centres[k + 1] - centres[k])
    }
    return centres.length - 1
  }

  let target = sceneFromScroll()
  let current = reduceMotion ? Math.round(target) : target
  let wanted = true
  const pointer = { x: 0, y: 0, tx: 0, ty: 0 }
  const started = performance.now()

  window.addEventListener(
    'scroll',
    () => {
      target = sceneFromScroll()
      wanted = true
    },
    { passive: true },
  )
  new ResizeObserver(resize).observe(stage)
  if (finePointer && !reduceMotion) {
    window.addEventListener(
      'pointermove',
      (event) => {
        pointer.tx = (event.clientX / window.innerWidth - 0.5) * 0.16
        pointer.ty = (event.clientY / window.innerHeight - 0.5) * -0.1
        wanted = true
      },
      { passive: true },
    )
  }

  let visible = true
  new IntersectionObserver((entries) => {
    visible = entries[0].isIntersecting
    if (visible) wanted = true
  }).observe(story)

  resize()

  function frame(now) {
    requestAnimationFrame(frame)
    if (!visible) return
    const next = reduceMotion ? Math.round(target) : current + (target - current) * 0.09
    pointer.x += (pointer.tx - pointer.x) * 0.06
    pointer.y += (pointer.ty - pointer.y) * 0.06
    const moving =
      Math.abs(next - current) > 0.0004 ||
      Math.abs(pointer.tx - pointer.x) > 0.0004 ||
      Math.abs(pointer.ty - pointer.y) > 0.0004
    // The pile turns very slowly while it waits at the top of the page.
    const swayAmount = reduceMotion ? 0 : 1 - MathUtils.smoothstep(current, 0.2, 0.8)
    if (!moving && !wanted && swayAmount === 0) return
    current = Math.abs(target - next) < 0.0004 ? target : next
    const t = (now - started) / 1000
    pose(current, {
      x: pointer.x,
      y: pointer.y,
      sway: Math.sin(t * 0.35) * 0.05 * swayAmount,
    })
    renderer.render(scene, camera)
    wanted = false
  }
  requestAnimationFrame(frame)
  document.documentElement.classList.add('has-scene')
}
