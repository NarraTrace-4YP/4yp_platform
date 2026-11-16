// Starting a pipeline from the app and following it until it finishes.
import { useEffect, useRef, useState } from 'react'
import { backend } from './wmill'
import { RunStatus, message, recall, remember } from './shared'

const POLL_MS = 3000
const FINISHED = ['done', 'failed', 'cancelled']

export type Run = {
  jobId: string | null
  run: RunStatus | null
  error: string | null
  starting: boolean
  active: boolean
  start: (startJob: () => Promise<string>) => Promise<void>
  checkAgain: () => void
  clear: () => void
}

// key: which card this is. The job id is kept in the browser, so a run started before a page
// reload is picked up again. onFinished runs once when the run ends (to refresh the status).
export function useRun(key: string, onFinished: () => void): Run {
  const [jobId, setJobId] = useState<string | null>(() => recall<string>(`run:${key}`))
  const [run, setRun] = useState<RunStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const finished = useRef(onFinished)
  finished.current = onFinished

  useEffect(() => {
    if (!jobId) return
    let stopped = false
    let timer: ReturnType<typeof setTimeout> | undefined
    async function poll() {
      try {
        const status: RunStatus = await backend.run_status({ job_id: jobId })
        if (stopped) return
        setRun(status)
        setError(null)
        if (FINISHED.includes(status.state)) {
          finished.current()
          return
        }
      } catch (e) {
        if (!stopped) setError(message(e))
        return // stop following; "Check again" restarts it
      }
      timer = setTimeout(poll, POLL_MS)
    }
    poll()
    return () => {
      stopped = true
      if (timer) clearTimeout(timer)
    }
  }, [jobId, attempt])

  async function start(startJob: () => Promise<string>) {
    setStarting(true)
    setError(null)
    setRun(null)
    try {
      const id = await startJob()
      if (!id) throw new Error('Windmill didn’t start the run')
      remember(`run:${key}`, id)
      setJobId(id)
    } catch (e) {
      setError(message(e))
    } finally {
      setStarting(false)
    }
  }

  function clear() {
    remember(`run:${key}`, null)
    setJobId(null)
    setRun(null)
    setError(null)
  }

  const active = Boolean(jobId) && !error && (!run || !FINISHED.includes(run.state))
  return { jobId, run, error, starting, active, start, checkAgain: () => setAttempt((n) => n + 1), clear }
}
