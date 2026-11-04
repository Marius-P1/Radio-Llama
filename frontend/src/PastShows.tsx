import { useEffect, useState } from 'react'
import { listShows, type ShowSummary } from './api'
import { dateLabel, languageLabel, personaLabel, statusLabel, styleLabel } from './labels'

type Props = {
  onOpen: (id: string) => void
}

export function PastShows({ onOpen }: Props) {
  const [shows, setShows] = useState<ShowSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    listShows()
      .then((loaded) => {
        if (!cancelled) setShows(loaded)
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message)
      })
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <section className="card">
      <h2>Past shows</h2>
      {error !== null && <p className="error">Could not load past shows: {error}</p>}
      {shows === null && error === null && <p className="muted">Loading...</p>}
      {shows !== null && shows.length === 0 && <p className="muted">No shows yet.</p>}
      {shows !== null && shows.length > 0 && (
        <ul className="shows">
          {shows.map((show) => (
            <li key={show.id}>
              <ShowRow show={show} onOpen={onOpen} />
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function ShowRow({ show, onOpen }: { show: ShowSummary; onOpen: (id: string) => void }) {
  const summary = (
    <>
      <span className="show-date">{dateLabel(show.created_at)}</span>
      <span className="show-meta">
        {styleLabel(show.config.style)}, {personaLabel(show.config.persona)} host,{' '}
        {languageLabel(show.config.language)}
      </span>
      <span className={`status status-${show.status}`}>{statusLabel(show.status)}</span>
    </>
  )

  if (show.status === 'ready') {
    return (
      <button type="button" className="show-row" onClick={() => onOpen(show.id)}>
        {summary}
      </button>
    )
  }

  return (
    <div className="show-row">
      {summary}
      {show.status === 'failed' && show.error && (
        <span className="muted show-error">{show.error}</span>
      )}
    </div>
  )
}
