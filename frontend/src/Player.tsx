import { useEffect, useState } from 'react'
import { audioUrl, getShow, type ShowResponse, type Track } from './api'
import {
  displayTitle,
  factSourceLabel,
  languageLabel,
  personaLabel,
  sourceTag,
  styleLabel,
} from './labels'
import { Script } from './Script'
import { SegmentPlayer } from './SegmentPlayer'

type Props = {
  id: string
  onBack: () => void
}

export function Player({ id, onBack }: Props) {
  const [show, setShow] = useState<ShowResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

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

  return (
    <section className="card">
      <button type="button" className="back" onClick={onBack}>
        Back
      </button>

      <h2>
        {/* A YouTube show has no style (D039): its wish is shown on the line below. */}
        {show.config.style && <>{styleLabel(show.config.style)} show, </>}
        {personaLabel(show.config.persona)} host, {languageLabel(show.config.language)}
        {sourceTag(show.config.source) && (
          <>
            {' '}
            <span className="tag">{sourceTag(show.config.source)}</span>
          </>
        )}
        {show.config.demo && (
          <>
            {' '}
            <span className="tag">Demo</span>
          </>
        )}
      </h2>
      {show.config.wish && <p className="muted">You asked for: {show.config.wish}</p>}

      {/* A ready show plays as one MP3, or segment by segment with YouTube (D038). */}
      {show.status === 'ready' && show.playback === 'segments' ? (
        <SegmentPlayer key={show.id} show={show} />
      ) : (
        <FilePlayer show={show} />
      )}

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

/** The single MP3 of a file show, with the script highlighted as it plays. */
function FilePlayer({ show }: { show: ShowResponse }) {
  const [currentTime, setCurrentTime] = useState(0)
  const tracksById = new Map(show.tracks.map((track) => [track.id, track]))

  // The segment under the play head: each one starts where the previous one ends.
  let currentIndex = -1
  let start = 0
  for (const [i, segment] of show.segments.entries()) {
    const end = start + (segment.duration_s ?? 0)
    if (currentTime >= start && currentTime < end) {
      currentIndex = i
      break
    }
    start = end
  }

  return (
    <>
      {show.status === 'ready' ? (
        <audio
          controls
          src={audioUrl(show.id)}
          onTimeUpdate={(event) => setCurrentTime(event.currentTarget.currentTime)}
        />
      ) : (
        <p className="muted">This show has no audio to play.</p>
      )}
      <Script segments={show.segments} tracks={tracksById} currentIndex={currentIndex} />
    </>
  )
}

/** The distinct pages the track's facts come from, in order, each with a short label. */
function factSources(track: Track): { url: string; label: string }[] {
  const sources: { url: string; label: string }[] = []
  for (const fact of track.facts ?? []) {
    if (fact.source_url === '' || sources.some((source) => source.url === fact.source_url)) {
      continue
    }
    sources.push({ url: fact.source_url, label: factSourceLabel(fact.source_type, fact.source_url) })
  }
  return sources
}

function Credit({ track }: { track: Track }) {
  const release = [track.album, track.year].filter((part) => part).join(', ')
  const sources = factSources(track)
  return (
    <>
      <strong>{displayTitle(track)}</strong> by{' '}
      {/* The artist link is the channel for a YouTube track, the artist page elsewhere. */}
      <a href={track.artist_url} target="_blank" rel="noreferrer">
        {track.artist}
      </a>
      {release !== '' && <> ({release})</>},{' '}
      {track.video_id ? (
        <a href={track.track_url} target="_blank" rel="noreferrer">
          Watch on YouTube
        </a>
      ) : (
        <>
          <a href={track.license_url} target="_blank" rel="noreferrer">
            {track.license}
          </a>{' '}
          (
          <a href={track.track_url} target="_blank" rel="noreferrer">
            source
          </a>
          )
        </>
      )}
      {sources.length > 0 && (
        <span className="credit-facts">
          Facts:{' '}
          {sources.map((source, i) => (
            <span key={source.url}>
              {i > 0 && ', '}
              <a href={source.url} target="_blank" rel="noreferrer">
                {source.label}
              </a>
            </span>
          ))}
        </span>
      )}
    </>
  )
}
