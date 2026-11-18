// Each deployment writes its own public app URL; share IDs differ between instances.
;(() => {
  function resolveObjectiveUrl(url) {
    const target = new URL(url)
    const expectedHost = `windmill.${window.location.hostname}`

    // Only embed this deployment's public DHR apps, even if the config is incorrect.
    if (target.host !== expectedHost || !target.pathname.startsWith('/public/dhr/')) {
      throw new Error('embed configuration contains an unexpected Windmill URL')
    }
    return target.href
  }

  async function loadObjectiveApp(frame) {
    try {
      const response = await fetch(frame.dataset.objectiveEmbed, { credentials: 'same-origin' })
      if (!response.ok) {
        throw new Error(`embed configuration returned ${response.status}`)
      }
      const { url } = await response.json()
      const objectiveUrl = resolveObjectiveUrl(url)
      frame.addEventListener('load', () => frame.classList.add('is-loaded'), { once: true })
      frame.src = objectiveUrl
    } catch (error) {
      console.error('Could not load the objective app', error)
      const errorMessage = document.createElement('p')
      errorMessage.className = 'embed-error'
      errorMessage.textContent =
        'The objective app is not available yet. Ask an administrator to refresh the deployment.'
      frame.replaceWith(errorMessage)
    }
  }

  const objectiveFrame = document.querySelector('[data-objective-embed]')
  if (objectiveFrame) loadObjectiveApp(objectiveFrame)
})()
