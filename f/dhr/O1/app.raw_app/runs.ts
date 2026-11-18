// Persist each run's job ID and poll its status until it completes or polling fails.
import { useEffect, useRef, useState } from 'react'
import { backend } from './wmill'
import { RunStatus, errorMessage, readStoredValue, writeStoredValue } from './shared'

const RUN_POLL_INTERVAL_MS = 3000
const TERMINAL_RUN_STATES = ['done', 'failed', 'cancelled']

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

// Each run card has its own storage key so reloading the page resumes the correct job.
// Keep the latest completion callback in a ref so rerenders do not restart polling.
export function useRun(key: string, onFinished: () => void): Run {
  const [jobId, setJobId] = useState<string | null>(() => readStoredValue<string>(`run:${key}`))
  const [run, setRun] = useState<RunStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const onFinishedRef = useRef(onFinished)
  onFinishedRef.current = onFinished

  useEffect(() => {
    if (!jobId) return
    let isDisposed = false
    let timer: ReturnType<typeof setTimeout> | undefined
    async function poll() {
      try {
        const status: RunStatus = await backend.run_status({ job_id: jobId })
        // Ignore responses from a previous job or an unmounted card.
        if (isDisposed) return
        setRun(status)
        setError(null)
        if (TERMINAL_RUN_STATES.includes(status.state)) {
          onFinishedRef.current()
          return
        }
      } catch (e) {
        if (!isDisposed) setError(errorMessage(e))
        // Leave the job ID intact so "Check again" can resume after a network error.
        return
      }
      timer = setTimeout(poll, RUN_POLL_INTERVAL_MS)
    }
    poll()
    return () => {
      isDisposed = true
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
      writeStoredValue(`run:${key}`, id)
      setJobId(id)
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setStarting(false)
    }
  }

  function clear() {
    writeStoredValue(`run:${key}`, null)
    setJobId(null)
    setRun(null)
    setError(null)
  }

  const active = Boolean(jobId) && !error && (!run || !TERMINAL_RUN_STATES.includes(run.state))
  return {
    jobId,
    run,
    error,
    starting,
    active,
    start,
    checkAgain: () => setAttempt((n) => n + 1),
    clear,
  }
}
