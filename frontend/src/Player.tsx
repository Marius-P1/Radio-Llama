import { useEffect, useMemo, useState } from 'react'
import { audioUrl, getShow, type Segment, type ShowResponse, type Track } from './api'
import { languageLabel, styleLabel } from './labels'

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
      <section>
        <p className="error">Could not load the show: {error}</p>
        <button type="button" onClick={onBack}>
          Back
        </button>
      </section>
    )
  }

  if (show === null) {
    return (
      <section>
        <p className="muted">Loading the show...</p>
      </section>
    )
  }

  const tracksById = new Map(show.tracks.map((track) => [track.id, track]))

  return (
    <section className="player">
      <button type="button" className="back" onClick={onBack}>
        Back
      </button>

      <h2>
        {styleLabel(show.config.style)} show, {show.config.persona} host,{' '}
        {languageLabel(show.config.language)}
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
            <SegmentText
              segment={segment}
              track={segment.track_id ? tracksById.get(segment.track_id) : undefined}
            />
          </li>
        ))}
      </ol>

      <h3>Tracks and credits</h3>
      <ul className="credits">
        {show.tracks.map((track) => (
          <li key={track.id}>
            <strong>{track.title}</strong> by {track.artist} (
            <a href={track.track_url} target="_blank" rel="noreferrer">
              source
            </a>
            ), {track.license}
          </li>
        ))}
      </ul>

      <footer>
        <p className="muted">
          Music under Creative Commons licenses, credited above. Voice by ElevenLabs.
        </p>
      </footer>
    </section>
  )
}

function SegmentText({ segment, track }: { segment: Segment; track: Track | undefined }) {
  if (segment.kind === 'song') {
    const title = track?.title ?? 'a song'
    const artist = track?.artist ?? 'an unknown artist'
    return (
      <em>
        Now playing: {title} by {artist}
      </em>
    )
  }
  return <p>{segment.text}</p>
}
