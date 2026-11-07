import { useEffect, useMemo, useState } from 'react'
import { audioUrl, getShow, type Segment, type ShowResponse, type Track } from './api'
import { languageLabel, personaLabel, styleLabel } from './labels'
import { formatTime } from './time'

type Props = {
  id: string
  onBack: () => void
}

export function Player({ id, onBack }: Props) {
  const [show, setShow] = useState<ShowResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [currentTime, setCurrentTime] = useState(0)

  useEffect(() => {
    let cancelled = false
    getShow(id)
      .then((loaded) => {
        if (!cancelled) setShow(loaded)
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message)
      })
    return () => {
      cancelled = true
    }
  }, [id])

  // Each segment starts where the previous one ends.
  const starts = useMemo(() => {
    const result: number[] = []
    let offset = 0
    for (const segment of show?.segments ?? []) {
      result.push(offset)
      offset += segment.duration_s ?? 0
    }
    return result
  }, [show])

  const currentIndex = (show?.segments ?? []).findIndex((segment, i) => {
    const start = starts[i]
    const end = start + (segment.duration_s ?? 0)
    return currentTime >= start && currentTime < end
  })

  if (error !== null) {
    return (
      <section className="card">
        <button type="button" className="back" onClick={onBack}>
          Back
        </button>
        <p className="error">Could not load the show: {error}</p>
      </section>
    )
  }

  if (show === null) {
    return (
      <section className="card">
        <p className="muted">Loading the show...</p>
      </section>
    )
  }

  const tracksById = new Map(show.tracks.map((track) => [track.id, track]))

  return (
    <section className="card">
      <button type="button" className="back" onClick={onBack}>
        Back
      </button>

      <h2>
        {styleLabel(show.config.style)} show, {personaLabel(show.config.persona)} host,{' '}
        {languageLabel(show.config.language)}
        {show.config.demo && (
          <>
            {' '}
            <span className="tag">Demo</span>
          </>
        )}
      </h2>

      {show.status === 'ready' ? (
        <audio
          controls
          src={audioUrl(show.id)}
          onTimeUpdate={(event) => setCurrentTime(event.currentTarget.currentTime)}
        />
      ) : (
        <p className="muted">This show has no audio to play.</p>
      )}

      <h3>Script</h3>
      <ol className="script">
        {show.segments.map((segment, i) => (
          <li key={i} className={i === currentIndex ? 'segment current' : 'segment'}>
            {/* A failed show has no durations: then there is no range to show. */}
            {typeof segment.duration_s === 'number' && (
              <span className="segment-time">
                <span>{formatTime(starts[i])}</span>
                <span className="segment-time-dash"> - </span>
                <span>{formatTime(starts[i] + segment.duration_s)}</span>
              </span>
            )}
            <div className="segment-text">
              <SegmentText
                segment={segment}
                track={segment.track_id ? tracksById.get(segment.track_id) : undefined}
              />
            </div>
          </li>
        ))}
      </ol>

      <h3>Tracks and credits</h3>
      <ul className="credits">
        {show.tracks.map((track) => (
          <li key={track.id}>
            <Credit track={track} />
          </li>
        ))}
      </ul>
    </section>
  )
}

/** The title to show and speak: the spoken title when the catalog sets one. */
function displayTitle(track: Track): string {
  return track.spoken_title ?? track.title
}

function Credit({ track }: { track: Track }) {
  const release = [track.album, track.year].filter((part) => part).join(', ')
  return (
    <>
      <strong>{displayTitle(track)}</strong> by{' '}
      <a href={track.artist_url} target="_blank" rel="noreferrer">
        {track.artist}
      </a>
      {release !== '' && <> ({release})</>},{' '}
      <a href={track.license_url} target="_blank" rel="noreferrer">
        {track.license}
      </a>{' '}
      (
      <a href={track.track_url} target="_blank" rel="noreferrer">
        source
      </a>
      )
    </>
  )
}

function SegmentText({ segment, track }: { segment: Segment; track: Track | undefined }) {
  if (segment.kind === 'song') {
    const title = track ? displayTitle(track) : 'a song'
    const artist = track?.artist ?? 'an unknown artist'
    return (
      <em>
        Now playing: {title} by {artist}
      </em>
    )
  }
  return <p>{segment.text}</p>
}
