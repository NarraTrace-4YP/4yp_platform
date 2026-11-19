// Behaviour shared by every page. Everything here is an enhancement: without it the pages
// still read in full, nothing is hidden, and nothing moves.
;(() => {
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches

  // The masthead sits over the reading scene on the project page until the scene ends.
  const masthead = document.querySelector('.masthead.is-over-scene')
  const story = document.querySelector('.story')
  if (masthead && story) {
    const update = () => {
      const passed = story.getBoundingClientRect().bottom <= masthead.offsetHeight + 1
      masthead.classList.toggle('is-over-scene', !passed)
    }
    window.addEventListener('scroll', update, { passive: true })
    window.addEventListener('resize', update, { passive: true })
    update()
  }

  // Highlight a heading line by line, like a highlighter pen. The lines are found by
  // measuring where each word lands, so the heading is split again if the width changes.
  const splitIntoLines = (heading) => {
    const text = heading.dataset.sweepText || heading.textContent.replace(/\s+/g, ' ').trim()
    heading.dataset.sweepText = text
    heading.setAttribute('aria-label', text)
    heading.textContent = ''
    const words = text.split(' ').map((word) => {
      const span = document.createElement('span')
      span.textContent = word
      heading.append(span, ' ')
      return span
    })
    const lines = []
    words.forEach((word) => {
      const top = Math.round(word.offsetTop)
      const line = lines[lines.length - 1]
      if (line && Math.abs(line.top - top) < 4) line.words.push(word.textContent)
      else lines.push({ top, words: [word.textContent] })
    })
    heading.textContent = ''
    lines.forEach((line, index) => {
      const span = document.createElement('span')
      span.className = 'sweep-line'
      span.setAttribute('aria-hidden', 'true')
      span.style.setProperty('--line', String(index))
      span.textContent = line.words.join(' ')
      heading.append(span)
      if (index < lines.length - 1) heading.append(document.createElement('br'))
    })
  }

  const sweeps = [...document.querySelectorAll('[data-sweep]')]
  if (sweeps.length) {
    const run = () => {
      sweeps.forEach((heading) => {
        splitIntoLines(heading)
        requestAnimationFrame(() => requestAnimationFrame(() => heading.classList.add('is-swept')))
      })
    }
    ;(document.fonts ? document.fonts.ready : Promise.resolve()).then(run)
    let width = window.innerWidth
    let timer = 0
    window.addEventListener('resize', () => {
      if (Math.abs(window.innerWidth - width) < 40) return
      width = window.innerWidth
      clearTimeout(timer)
      timer = setTimeout(() => sweeps.forEach(splitIntoLines), 150)
    })
  }

  // Small figures drawn from their data attributes: the HALT report and the pilot sample.
  document.querySelectorAll('.halt-dots[data-total]').forEach((grid) => {
    const total = Number(grid.dataset.total)
    const used = Number(grid.dataset.used)
    for (let index = 0; index < total; index += 1) {
      const dot = document.createElement('i')
      if (index < used) dot.className = 'used'
      grid.append(dot)
    }
  })
  document.querySelectorAll('.sample[data-total]').forEach((grid) => {
    const total = Number(grid.dataset.total)
    const pilot = Number(grid.dataset.pilot)
    for (let index = 0; index < total; index += 1) {
      const page = document.createElement('i')
      if (index < pilot) page.className = 'pilot'
      grid.append(page)
    }
  })

  // A chapter's key sentence is highlighted once the chapter has been read to its middle,
  // and stays highlighted, like a passage marked in a review.
  const chapters = [...document.querySelectorAll('.chapter')].filter((chapter) =>
    chapter.querySelector('.reading'),
  )
  if (chapters.length) {
    if (reduceMotion || !('IntersectionObserver' in window)) {
      chapters.forEach((chapter) => chapter.classList.add('is-read'))
    } else {
      const observer = new IntersectionObserver(
        (entries) => {
          entries.forEach((entry) => {
            if (!entry.isIntersecting) return
            entry.target.classList.add('is-read')
            observer.unobserve(entry.target)
          })
        },
        { rootMargin: '-35% 0px -35% 0px' },
      )
      chapters.forEach((chapter) => observer.observe(chapter))
    }
  }

  // The stage rail marks the stage being read.
  const rail = document.querySelector('.stage-rail')
  if (rail && 'IntersectionObserver' in window) {
    const items = new Map(
      [...rail.querySelectorAll('a[href^="#"]')].map((link) => [
        link.getAttribute('href').slice(1),
        link.parentElement,
      ]),
    )
    const panels = [...items.keys()].map((id) => document.getElementById(id)).filter(Boolean)
    const mark = (id) =>
      items.forEach((item, key) => item.classList.toggle('is-current', key === id))
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) mark(entry.target.id)
        })
      },
      { rootMargin: '-45% 0px -50% 0px' },
    )
    panels.forEach((panel) => observer.observe(panel))
    mark(panels[0]?.id)
  }
})()
