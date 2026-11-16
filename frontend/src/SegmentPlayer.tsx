import { useEffect, useRef, useState } from 'react'
import type { ShowResponse } from './api'
import { Script } from './Script'

// The player of a YouTube show (D038): the host's segments play from their own files in
// one <audio> element, the songs in one embedded YouTube player, in script order.
// Both are created once, when this view mounts, and reused for the whole show. Safari
// misses the user's gesture for an iframe created inside the click, so the click on
// Play only starts the first medium.

type Props = {
  show: ShowResponse
}

// idle: nothing started yet. blocked: the browser refused to start a medium without a
// gesture; Continue resumes it from a click. ended: the last segment finished.
type Phase = 'idle' | 'playing' | 'paused' | 'blocked' | 'ended'

// A song that cannot play is marked in the script, then the show goes on after this pause.
const SKIP_PAUSE_MS = 1500

let apiLoading: Promise<typeof YT> | null = null

/** Load https://www.youtube.com/iframe_api once per page and resolve when it is usable. */
function loadYouTubeApi(): Promise<typeof YT> {
  if (apiLoading === null) {
    apiLoading = new Promise((resolve, reject) => {
      window.onYouTubeIframeAPIReady = () => {
        if (window.YT) resolve(window.YT)
        else reject(new Error('The YouTube player did not load.'))
      }
      const script = document.createElement('script')
      script.src = 'https://www.youtube.com/iframe_api'
      script.onerror = () => reject(new Error('The YouTube player did not load.'))
      document.head.appendChild(script)
    })
  }
  return apiLoading
}

/** The YouTube video of segment `i`, null unless it is a song with a video. */
function videoIdOf(show: ShowResponse, i: number): string | null {
  const segment = show.segments[i]
  if (segment === undefined || segment.kind !== 'song' || !segment.track_id) return null
  return show.tracks.find((track) => track.id === segment.track_id)?.video_id ?? null
}

/** The first video at or after segment `from`, null when no song is left. */
function nextVideoId(show: ShowResponse, from: number): string | null {
  for (let i = from; i < show.segments.length; i++) {
    const videoId = videoIdOf(show, i)
    if (videoId !== null) return videoId
  }
  return null
}

export function SegmentPlayer({ show }: Props) {
  const boxRef = useRef<HTMLDivElement>(null)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const playerRef = useRef<YT.Player | null>(null)
  const timerRef = useRef<number | null>(null)
  // True once the YouTube player is usable, or once it is known not to load: then the
  // songs are skipped and only the host plays.
  const [playerReady, setPlayerReady] = useState(false)
  const [phase, setPhaseState] = useState<Phase>('idle')
  const [index, setIndexState] = useState(0)
  // The media events fire between renders: cueing the next song inside startSegment
  // raises a state change at once, before React has rendered the new index and phase.
  // The handlers read these refs, which change together with the state.
  const phaseRef = useRef<Phase>('idle')
  const indexRef = useRef(0)
  const setPhase = (next: Phase) => {
    phaseRef.current = next
    setPhaseState(next)
  }
  const setIndex = (next: number) => {
    indexRef.current = next
    setIndexState(next)
  }
  // Indexes of the songs that could not play on YouTube.
  const [failed, setFailed] = useState<ReadonlySet<number>>(new Set())

  const segments = show.segments
  const tracksById = new Map(show.tracks.map((track) => [track.id, track]))

  const clearTimer = () => {
    if (timerRef.current !== null) window.clearTimeout(timerRef.current)
    timerRef.current = null
  }

  /** Mark segment `i` as unplayable and go on with the next one after a short pause. */
  const skip = (i: number) => {
    setFailed((previous) => new Set(previous).add(i))
    timerRef.current = window.setTimeout(() => actions.current.startSegment(i + 1), SKIP_PAUSE_MS)
  }

  const startSegment = (i: number) => {
    clearTimer()
    if (i >= segments.length) {
      setPhase('ended')
      setIndex(0)
      return
    }
    setIndex(i)
    setPhase('playing')
    const segment = segments[i]
    const audio = audioRef.current
    const player = playerRef.current
    if (segment.kind === 'song') {
      audio?.pause()
      const videoId = videoIdOf(show, i)
      if (player === null || videoId === null) {
        skip(i)
        return
      }
      // A demo show plays each song for the cut the backend wrote into the segment (D023).
      if (show.config.demo && segment.duration_s) {
        player.loadVideoById({ videoId, endSeconds: segment.duration_s })
      } else player.loadVideoById(videoId)
      return
    }
    if (audio === null || !segment.audio_url) {
      startSegment(i + 1)
      return
    }
    // While the host speaks, the box shows the next song, cued. Cueing also stops a
    // video that was still playing when Next was pressed.
    const upcoming = nextVideoId(show, i)
    if (upcoming !== null) player?.cueVideoById(upcoming)
    else player?.pauseVideo()
    audio.src = segment.audio_url
    audio.play().catch(() => setPhase('blocked'))
  }

  const pause = () => {
    clearTimer()
    audioRef.current?.pause()
    playerRef.current?.pauseVideo()
    setPhase('paused')
  }

  const resume = () => {
    if (failed.has(index)) {
      startSegment(index + 1)
      return
    }
    setPhase('playing')
    if (segments[index]?.kind === 'song') playerRef.current?.playVideo()
    else audioRef.current?.play().catch(() => setPhase('blocked'))
  }

  const togglePlay = () => {
    if (phase === 'idle' || phase === 'ended') {
      // A fresh run: a video that failed once may play now.
      setFailed(new Set())
      startSegment(0)
    } else if (phase === 'playing') pause()
    else resume()
  }

  const next = () => startSegment(index + 1)

  // The media events are bound once, when the elements are created, so they go through
  // this ref to reach the handlers of the latest render. The index and the phase come
  // from the refs: a state change raised while a segment starts must see that segment.
  const handlers = {
    startSegment,
    onAudioEnded: () => {
      const i = indexRef.current
      if (phaseRef.current === 'playing' && segments[i]?.kind !== 'song') startSegment(i + 1)
    },
    onStateChange: (state: number) => {
      const i = indexRef.current
      const now = phaseRef.current
      // Cueing the next song while the host speaks raises states too: not this segment's.
      if (segments[i]?.kind !== 'song') return
      if (state === YT.PlayerState.ENDED && now === 'playing') startSegment(i + 1)
      // The listener may also use the player's own controls.
      else if (state === YT.PlayerState.PLAYING && (now === 'paused' || now === 'blocked')) {
        setPhase('playing')
      } else if (state === YT.PlayerState.PAUSED && now === 'playing') setPhase('paused')
    },
    onError: () => {
      const i = indexRef.current
      // The player can report the same video twice: one skip per song.
      if (failed.has(i) || segments[i]?.kind !== 'song') return
      if (phaseRef.current === 'playing' || phaseRef.current === 'blocked') skip(i)
    },
    onBlocked: () => setPhase('blocked'),
  }
  const actions = useRef(handlers)
  useEffect(() => {
    actions.current = handlers
  })

  useEffect(() => {
    const audio = new Audio()
    audio.preload = 'auto'
    audio.addEventListener('ended', () => actions.current.onAudioEnded())
    audioRef.current = audio

    const box = boxRef.current
    let cancelled = false
    let player: YT.Player | null = null
    loadYouTubeApi()
      .then((api) => {
        if (cancelled || box === null) return
        // The API replaces this element with its iframe, inside the box React owns.
        const target = document.createElement('div')
        box.appendChild(target)
        player = new api.Player(target, {
          videoId: nextVideoId(show, 0) ?? undefined,
          playerVars: { playsinline: 1 },
          events: {
            onReady: () => {
              if (cancelled) return
              playerRef.current = player
              setPlayerReady(true)
            },
            onStateChange: (event) => actions.current.onStateChange(event.data),
            onError: () => actions.current.onError(),
            onAutoplayBlocked: () => actions.current.onBlocked(),
          },
        })
      })
      .catch(() => {
        if (!cancelled) setPlayerReady(true)
      })

    return () => {
      cancelled = true
      if (timerRef.current !== null) window.clearTimeout(timerRef.current)
      audio.pause()
      audio.removeAttribute('src')
      audioRef.current = null
      player?.destroy()
      playerRef.current = null
      box?.replaceChildren()
    }
    // Created once per show: the handlers read the latest render through the actions ref.
  }, [show])

  const current = segments[index]
  let status: string
  if (!playerReady) status = 'Loading the YouTube player...'
  else if (phase === 'idle') status = 'Press Play to start the show.'
  else if (phase === 'ended') status = 'The show has ended. Press Play to hear it again.'
  else if (phase === 'blocked') status = 'The browser did not start the sound. Press Continue.'
  else if (current?.kind === 'song') status = 'The song plays in the YouTube player.'
  else status = 'The host speaks.'

  return (
    <>
      {/* The YouTube player stays visible while the show plays: a policy of the embed. */}
      <div className="video-box" ref={boxRef} />
      <div className="player-controls">
        <button type="button" className="primary" disabled={!playerReady} onClick={togglePlay}>
          {phase === 'playing' ? 'Pause' : 'Play'}
        </button>
        <button
          type="button"
          className="choice"
          disabled={phase === 'idle' || phase === 'ended'}
          onClick={next}
        >
          Next
        </button>
        {phase === 'blocked' && (
          <button type="button" className="choice" onClick={resume}>
            Continue
          </button>
        )}
      </div>
      <p className="muted player-status">{status}</p>
      <Script
        segments={segments}
        tracks={tracksById}
        currentIndex={phase === 'idle' || phase === 'ended' ? -1 : index}
        failed={failed}
      />
    </>
  )
}
