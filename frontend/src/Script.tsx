import { useMemo } from 'react'
import type { Segment, Track } from './api'
import { displayTitle } from './labels'
import { formatTime } from './time'

type Props = {
  segments: Segment[]
  tracks: ReadonlyMap<string, Track>
  // The segment to highlight, -1 for none.
  currentIndex: number
  // Segments that could not play: a video YouTube refused, a file that did not load
  // (sequenced player only).
  failed?: ReadonlySet<number>
}

const NONE: ReadonlySet<number> = new Set()

/** The script of a show as a list, one segment per row with its time range. */
export function Script({ segments, tracks, currentIndex, failed = NONE }: Props) {
  // Each segment starts where the previous one ends.
  const starts = useMemo(() => {
    const result: number[] = []
    let offset = 0
    for (const segment of segments) {
      result.push(offset)
      offset += segment.duration_s ?? 0
    }
    return result
  }, [segments])

  return (
    <>
      <h3>Script</h3>
      <ol className="script">
        {segments.map((segment, i) => (
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
                track={segment.track_id ? tracks.get(segment.track_id) : undefined}
              />
              {failed.has(i) && <span className="segment-note">Could not play.</span>}
            </div>
          </li>
        ))}
      </ol>
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
