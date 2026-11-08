import { useEffect, useState } from 'react'
import { getShow, type ShowResponse } from './api'
import { sourceTag } from './labels'

type Props = {
  id: string
  onReady: (id: string) => void
  onBack: () => void
}

export function Progress({ id, onReady, onBack }: Props) {
  const [show, setShow] = useState<ShowResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    let timer: number | undefined

    const poll = async () => {
      try {
        const current = await getShow(id)
        if (cancelled) return
        setShow(current)
        // `status` is the end signal, not done == total: progress reaches 7/7 before assembly finishes.
        if (current.status === 'ready') {
          onReady(id)
          return
        }
        if (current.status === 'failed') return
        timer = window.setTimeout(poll, 1000)
      } catch (err) {
        if (!cancelled) setError((err as Error).message)
      }
    }

    poll()
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [id, onReady])

  const message =
    show?.status === 'failed' ? (show.error ?? 'Something went wrong.') : error

  if (message !== null) {
    return (
      <section className="card">
        <h2>The show could not be made</h2>
        <p className="error">{message}</p>
        <div className="actions">
          <button type="button" className="primary" onClick={onBack}>
            Try again
          </button>
        </div>
      </section>
    )
  }

  let line = 'Getting ready...'
  if (show?.status === 'generating') {
    line = show.progress
      ? `Writing and recording... ${show.progress.done}/${show.progress.total} segments`
      : 'Writing and recording...'
  }

  return (
    <section className="card">
      <h2>
        Making your show
        {show && sourceTag(show.config.source) && (
          <>
            {' '}
            <span className="tag">{sourceTag(show.config.source)}</span>
          </>
        )}
        {show?.config.demo && (
          <>
            {' '}
            <span className="tag">Demo</span>
          </>
        )}
      </h2>
      <p className="progress-line" aria-live="polite">
        {line}
      </p>
      <p className="muted">
        The host is writing the script and recording it. This takes a few minutes.
      </p>
    </section>
  )
}
