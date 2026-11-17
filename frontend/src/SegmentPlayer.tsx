import { useEffect, useRef, useState } from 'react'
import { endShow, nextPart, type Segment, type ShowResponse } from './api'
import { Script } from './Script'

// The player of a show that plays segment by segment (D038, D044). A segment with a file
// plays in one <audio> element: the host's speech, and the songs of an endless Jamendo
// show. A song without a file is a YouTube video and plays in one embedded YouTube
// player. Both are created once, when this view mounts, and reused for the whole show.
// Safari misses the user's gesture for an iframe created inside the click, so the click
// on Play only starts the first medium.
//
// An endless show grows while it plays (D042, D044). In live mode the player asks the
// backend for the next break and song when the last song it knows passes its middle,
// and the reply replaces the show. Segment indexes never change, so the new part is
// simply there when the song ends. The listener stops the show with "End after this
// song", and after an hour without a click the player asks "Still listening?" before
// it asks the backend for anything more.

type Props = {
  show: ShowResponse
  // True when the listener comes from the create flow. Only then does an endless show
  // grow: a show opened from Past shows is a replay and asks for nothing (D044).
  live: boolean
  // Called with the show and its new part. The parent owns the show, so the title
  // and the credits grow with the script.
  onShowChange: (show: ShowResponse) => void
}

// idle: nothing started yet. blocked: the browser refused to start a medium without a
// gesture; Continue resumes it from a click. waiting: a live show played its last song
// and the next part has not arrived; the show goes on by itself when it does.
// ended: the last segment finished.
type Phase = 'idle' | 'playing' | 'paused' | 'blocked' | 'waiting' | 'ended'

// The request for the next part of a live show. One at a time: none is in flight,
// one is, or the last one failed and its message is shown.
type Ask = { state: 'none' } | { state: 'pending' } | { state: 'failed'; message: string }

// "End after this song" (D042): the song the listener chose, and whether the backend's
// reply is still awaited. Null when it was not asked, or when the request failed.
type Ending = { after: number; pending: boolean }

// A song that cannot play is marked in the script, then the show goes on after this pause.
const SKIP_PAUSE_MS = 1500
// How often a live show looks at the position of its song, to ask for the next part.
const TICK_MS = 1000
// "Still listening?" (D042): after this long without a click the player stops asking
// for the next part until the listener presses the button, so an empty room spends
// nothing. An hour, or VITE_CHECK_IN_MINUTES at build time, to test it in seconds.
const CHECK_IN_MS = (Number(import.meta.env.VITE_CHECK_IN_MINUTES) || 60) * 60_000
// How long the IFrame API gets to call back. A privacy extension can serve a stub that
// never does: the show then plays without the songs instead of staying disabled.
const API_TIMEOUT_MS = 10_000

let apiLoading: Promise<typeof YT> | null = null

/** Load https://www.youtube.com/iframe_api once per page and resolve when it is usable. */
function loadYouTubeApi(): Promise<typeof YT> {
  // Checked on every call: a script that finished after the timeout is picked up.
  if (window.YT?.Player) return Promise.resolve(window.YT)
  if (apiLoading === null) {
    apiLoading = new Promise((resolve, reject) => {
      // A failed load is forgotten, so the next show tries again instead of running
      // host-only for the rest of the page.
      const fail = () => {
        apiLoading = null
        reject(new Error('The YouTube player did not load.'))
      }
      const timer = window.setTimeout(fail, API_TIMEOUT_MS)
      window.onYouTubeIframeAPIReady = () => {
        window.clearTimeout(timer)
        if (window.YT?.Player) resolve(window.YT)
        else fail()
      }
      const script = document.createElement('script')
      script.src = 'https://www.youtube.com/iframe_api'
      script.onerror = fail
      document.head.appendChild(script)
    })
  }
  return apiLoading
}

// Whether an unexpected play() failure was already logged: once per page is enough.
let playFailureLogged = false

/**
 * Start `audio`, `onBlocked` when the browser wants a gesture first. A pending play()
 * that pause() or a new src interrupts rejects with AbortError: that is not a refusal.
 */
function playAudio(audio: HTMLAudioElement, onBlocked: () => void) {
  audio.play().catch((error: unknown) => {
    const name = error instanceof DOMException ? error.name : ''
    if (name === 'AbortError') return
    if (name === 'NotAllowedError') {
      onBlocked()
    } else if (!playFailureLogged) {
      playFailureLogged = true
      console.warn('The host segment did not start.', error)
    }
  })
}

/**
 * An endless show the host has not closed: its last segment is not the goodbye (D044).
 * `=== true` because a show.json written before the endless show has no such field.
 */
function isOpen(show: ShowResponse): boolean {
  return show.config.endless === true && show.segments.at(-1)?.kind !== 'outro'
}

/** Whether the segment plays in the YouTube player: a song that has no file of its own. */
function isVideo(segment: Segment | undefined): boolean {
  return segment !== undefined && segment.kind === 'song' && !segment.audio_url
}

/** The YouTube video of segment `i`, null unless it is a song with a video. */
function videoIdOf(show: ShowResponse, i: number): string | null {
  const segment = show.segments[i]
  if (segment === undefined || !isVideo(segment) || !segment.track_id) return null
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

export function SegmentPlayer({ show, live, onShowChange }: Props) {
  const boxRef = useRef<HTMLDivElement>(null)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const playerRef = useRef<YT.Player | null>(null)
  const timerRef = useRef<number | null>(null)
  // The YouTube player and its box exist only for a show that has a video (D044): a
  // Jamendo show plays every segment from a file and never talks to YouTube.
  const hasVideos = show.tracks.some((track) => track.video_id)
  // True once the YouTube player is usable, or once it is known not to load: then the
  // songs are skipped and only the host plays. A show without videos is ready at once.
  const [playerReady, setPlayerReady] = useState(!hasVideos)
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
  // Indexes of the songs that could not play on YouTube. The ref is read by the error
  // handler: two error events in one frame must see the first one's mark.
  const [failed, setFailedState] = useState<ReadonlySet<number>>(new Set())
  const failedRef = useRef<ReadonlySet<number>>(new Set())
  const setFailed = (next: ReadonlySet<number>) => {
    failedRef.current = next
    setFailedState(next)
  }
  const markFailed = (i: number) => setFailed(new Set(failedRef.current).add(i))
  // A live show changes while it plays. The handlers read it from this ref, which
  // changes together with the parent's state: a reply that arrives while the player
  // waits starts the new segment at once, before React has rendered the new show.
  const showRef = useRef(show)
  const setShow = (next: ShowResponse) => {
    showRef.current = next
    onShowChange(next)
  }
  const [ask, setAskState] = useState<Ask>({ state: 'none' })
  const askRef = useRef<Ask>({ state: 'none' })
  const setAsk = (next: Ask) => {
    askRef.current = next
    setAskState(next)
  }
  // The number of segments the show had when the player last asked on its own. The
  // request goes once per song: a failed one is not sent again every second.
  const askedRef = useRef(-1)
  // Whether the one automatic retry of a failed step was used (D044). Reset by a reply.
  const retriedRef = useRef(false)
  // False once the view is gone: a reply that arrives later must not start anything.
  const aliveRef = useRef(false)
  const [ending, setEndingState] = useState<Ending | null>(null)
  const endingRef = useRef<Ending | null>(null)
  const setEnding = (next: Ending | null) => {
    endingRef.current = next
    setEndingState(next)
  }
  const [endError, setEndError] = useState<string | null>(null)
  // When the listener last clicked, as Date.now(), and whether "Still listening?" shows.
  const lastClickRef = useRef(0)
  const [checkIn, setCheckIn] = useState(false)

  const segments = show.segments
  const tracksById = new Map(show.tracks.map((track) => [track.id, track]))

  const clearTimer = () => {
    if (timerRef.current !== null) window.clearTimeout(timerRef.current)
    timerRef.current = null
  }

  /** Mark segment `i` as unplayable and go on with the next one after a short pause. */
  const skip = (i: number) => {
    clearTimer()
    markFailed(i)
    timerRef.current = window.setTimeout(() => actions.current.startSegment(i + 1), SKIP_PAUSE_MS)
  }

  /** Ask the backend for the next break and song (D044). One request at a time. */
  const askNext = () => {
    const current = showRef.current
    if (!live || !isOpen(current) || askRef.current.state === 'pending') return
    // The listener chose to end: nothing more is made.
    if (endingRef.current !== null) return
    // D042: no click for an hour, so maybe nobody listens. The button shows instead of
    // the request going, and the song that plays finishes. A click on anything gives
    // the next attempt its hour back.
    if (Date.now() - lastClickRef.current > CHECK_IN_MS) {
      setCheckIn(true)
      return
    }
    setCheckIn(false)
    const known = current.segments.length
    setAsk({ state: 'pending' })
    // The reply comes seconds later: it goes through the handlers of the latest render.
    nextPart(current.id, known).then(
      (reply) => actions.current.nextArrived(reply, known),
      (error: Error) => actions.current.nextFailed(error.message),
    )
  }

  /** The "Still listening?" button: the listener is there, so the show may go on. */
  const stillListening = () => {
    lastClickRef.current = Date.now()
    askNext()
  }

  /** "End after this song" (D042): the host says goodbye after the song the show reached. */
  const endAfterSong = () => {
    // Pressed twice: the first request stands.
    if (endingRef.current !== null) return
    const current = showRef.current
    const i = indexRef.current
    // The song that plays, or the one the host is introducing: a break always comes
    // with its song. On an open show the index only moves forward, so this is never an
    // earlier song, and the backend drops at most the one part made ahead.
    const after = current.segments[i]?.kind === 'song' ? i : i + 1
    if (current.segments[after]?.kind !== 'song') return
    setEnding({ after, pending: true })
    setEndError(null)
    endShow(current.id, after).then(
      (reply) => actions.current.endArrived(reply, after),
      (error: Error) => actions.current.endFailed(error.message),
    )
  }

  /**
   * Nothing can start yet: the last song is over, or was skipped, and the next part is
   * not there, or the goodbye of "End after this song" is still being written.
   */
  const wait = () => {
    // Next can be pressed in the middle of the last song: the song stops here.
    audioRef.current?.pause()
    playerRef.current?.pauseVideo()
    setPhase('waiting')
    // The reply of the end request moves the show on.
    if (endingRef.current?.pending) return
    // A step that failed while the song played gets its one automatic retry now. After
    // that only the Try again button asks: every request can cost an LLM call (D044).
    if (askRef.current.state === 'failed') {
      if (retriedRef.current) return
      retriedRef.current = true
    }
    askNext()
  }

  const startSegment = (i: number) => {
    clearTimer()
    const current = showRef.current
    // D044: while the end request is pending, nothing past the chosen song starts. The
    // backend is replacing what follows that song with the goodbye, under the same
    // segment index, so its audio is only loaded once the reply is in.
    const chosen = endingRef.current
    if (chosen?.pending && i > chosen.after) {
      wait()
      return
    }
    if (i >= current.segments.length) {
      // A live show that is still open has more to come. The index stays on the last
      // segment, so the show goes on from the one after it.
      if (live && isOpen(current)) {
        wait()
        return
      }
      setPhase('ended')
      setIndex(0)
      return
    }
    setIndex(i)
    setPhase('playing')
    const segment = current.segments[i]
    const audio = audioRef.current
    const player = playerRef.current
    if (isVideo(segment)) {
      audio?.pause()
      const videoId = videoIdOf(current, i)
      if (player === null || videoId === null) {
        skip(i)
        return
      }
      // A demo show plays each song for the cut the backend wrote into the segment (D023).
      if (current.config.demo && segment.duration_s) {
        player.loadVideoById({ videoId, endSeconds: segment.duration_s })
      } else player.loadVideoById(videoId)
      return
    }
    if (audio === null || !segment.audio_url) {
      startSegment(i + 1)
      return
    }
    // While a file plays, the box shows the next video, cued. Cueing also stops a
    // video that was still playing when Next was pressed.
    const upcoming = nextVideoId(current, i)
    if (upcoming !== null) player?.cueVideoById(upcoming)
    else player?.pauseVideo()
    audio.src = segment.audio_url
    playAudio(audio, () => setPhase('blocked'))
  }

  const pause = () => {
    clearTimer()
    audioRef.current?.pause()
    playerRef.current?.pauseVideo()
    setPhase('paused')
  }

  const resume = () => {
    // A song that failed while paused is not replayed: the show goes on.
    if (failedRef.current.has(index)) {
      startSegment(index + 1)
      return
    }
    setPhase('playing')
    const audio = audioRef.current
    if (isVideo(segments[index])) playerRef.current?.playVideo()
    else if (audio !== null) playAudio(audio, () => setPhase('blocked'))
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

  /** Once a second on a live show: ask for the next part when the song passes its middle. */
  const tick = () => {
    const current = showRef.current
    const i = indexRef.current
    const segment = current.segments[i]
    // Only while the last song the player knows is playing. A paused player asks for
    // nothing, and a song that already has a successor needs nothing.
    if (phaseRef.current !== 'playing' || segment?.kind !== 'song') return
    if (i !== current.segments.length - 1) return
    if (askedRef.current === current.segments.length) return
    // A demo song lasts 30 seconds, which a step can exceed: ask as soon as it starts.
    const middle = current.config.demo ? 0 : (segment.duration_s ?? 0) / 2
    const position = isVideo(segment)
      ? (playerRef.current?.getCurrentTime() ?? 0)
      : (audioRef.current?.currentTime ?? 0)
    if (position < middle) return
    askedRef.current = current.segments.length
    askNext()
  }

  /** The step failed. The song keeps playing and the message is shown (D044). */
  const nextFailed = (message: string) => {
    if (!aliveRef.current) return
    // The show was closed in the meantime: there is no next part to miss.
    if (!isOpen(showRef.current)) {
      setAsk({ state: 'none' })
      return
    }
    setAsk({ state: 'failed', message })
    // Nothing is playing any more, so the one automatic retry goes now.
    if (phaseRef.current === 'waiting' && !retriedRef.current) {
      retriedRef.current = true
      askNext()
    }
  }

  /** The reply of a `next` request sent when the show had `known` segments. */
  const nextArrived = (reply: ShowResponse, known: number) => {
    if (!aliveRef.current) return
    // A closed show never changes: a reply that comes in after the end reply would
    // put back the part the backend dropped.
    if (!isOpen(showRef.current)) {
      setAsk({ state: 'none' })
      return
    }
    // The backend answers with a longer show, or with one the host closed. Anything
    // else would make a waiting player ask again in a loop, so it counts as a failure.
    if (isOpen(reply) && reply.segments.length <= known) {
      nextFailed('The show did not get its next part.')
      return
    }
    setAsk({ state: 'none' })
    retriedRef.current = false
    setShow(reply)
    // The song ended before its successor was ready: the show goes on now.
    if (phaseRef.current === 'waiting') startSegment(indexRef.current + 1)
  }

  /** The show came back closed: the goodbye sits right after the chosen song. */
  const endArrived = (reply: ShowResponse, after: number) => {
    if (!aliveRef.current) return
    setEnding({ after, pending: false })
    // A step that failed before the end no longer matters: its message goes.
    setAsk({ state: 'none' })
    setShow(reply)
    // The song ended while the goodbye was being written: it plays now.
    if (phaseRef.current === 'waiting') startSegment(indexRef.current + 1)
  }

  /** The show could not be closed: the button comes back with the message (D044). */
  const endFailed = (message: string) => {
    if (!aliveRef.current) return
    setEnding(null)
    setEndError(message)
    // The player was holding the show back for the goodbye: it goes on as before.
    if (phaseRef.current === 'waiting') startSegment(indexRef.current + 1)
  }

  // The media events are bound once, when the elements are created, so they go through
  // this ref to reach the handlers of the latest render. The index, the phase and the
  // show come from the refs: a state change raised while a segment starts must see that
  // segment, and a show that just grew must be seen with its new part.
  const handlers = {
    startSegment,
    tick,
    nextArrived,
    nextFailed,
    endArrived,
    endFailed,
    onAudioEnded: () => {
      const i = indexRef.current
      const segment = showRef.current.segments[i]
      if (phaseRef.current === 'playing' && !isVideo(segment)) startSegment(i + 1)
    },
    onStateChange: (state: number) => {
      const i = indexRef.current
      const now = phaseRef.current
      // Cueing the next song while a file plays raises states too: not this segment's.
      if (!isVideo(showRef.current.segments[i])) return
      if (state === YT.PlayerState.ENDED && now === 'playing') startSegment(i + 1)
      // The listener may also use the player's own controls. The page never sees a
      // click made inside the iframe, so these two count as one for the check-in (D044).
      else if (state === YT.PlayerState.PLAYING && (now === 'paused' || now === 'blocked')) {
        lastClickRef.current = Date.now()
        setPhase('playing')
      } else if (state === YT.PlayerState.PAUSED && now === 'playing') {
        lastClickRef.current = Date.now()
        setPhase('paused')
      }
    },
    onError: () => {
      const i = indexRef.current
      // The player can report the same video twice: one mark per song.
      if (failedRef.current.has(i) || !isVideo(showRef.current.segments[i])) return
      // While paused the mark is enough: resume() then goes to the next segment.
      if (phaseRef.current === 'paused') markFailed(i)
      else skip(i)
    },
    onBlocked: () => setPhase('blocked'),
  }
  const actions = useRef(handlers)
  useEffect(() => {
    actions.current = handlers
  })

  useEffect(() => {
    aliveRef.current = true
    const audio = new Audio()
    audio.preload = 'auto'
    audio.addEventListener('ended', () => actions.current.onAudioEnded())
    audioRef.current = audio

    const box = boxRef.current
    let cancelled = false
    let player: YT.Player | null = null
    // Without a video the IFrame API is never loaded and there is no box to fill.
    const loading = hasVideos ? loadYouTubeApi() : null
    loading
      ?.then((api) => {
        if (cancelled || box === null) return
        // The API replaces this element with its iframe, inside the box React owns.
        const target = document.createElement('div')
        box.appendChild(target)
        player = new api.Player(target, {
          videoId: nextVideoId(showRef.current, 0) ?? undefined,
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
      aliveRef.current = false
      if (timerRef.current !== null) window.clearTimeout(timerRef.current)
      audio.pause()
      audio.removeAttribute('src')
      audioRef.current = null
      player?.destroy()
      playerRef.current = null
      box?.replaceChildren()
    }
    // Created once per show, by its id and not by the show object: a live show is
    // replaced by each reply, and that must not destroy the player in the middle of a
    // song (D044). The handlers read the latest render through the actions ref.
  }, [show.id, hasVideos])

  // The clock of a live show, and its record of the listener's clicks (D042): any press
  // of a pointer or of a key on the page. A replay has neither, so it never asks for
  // anything.
  useEffect(() => {
    if (!live) return
    const markClick = () => {
      lastClickRef.current = Date.now()
    }
    // The listener just pressed the button that made this show.
    markClick()
    document.addEventListener('pointerdown', markClick)
    document.addEventListener('keydown', markClick)
    const timer = window.setInterval(() => actions.current.tick(), TICK_MS)
    return () => {
      document.removeEventListener('pointerdown', markClick)
      document.removeEventListener('keydown', markClick)
      window.clearInterval(timer)
    }
  }, [live])

  const current = segments[index]
  // A segment is on: playing, paused or refused by the browser.
  const started = phase === 'playing' || phase === 'paused' || phase === 'blocked'
  const open = isOpen(show)
  // "End after this song" is offered once a live show plays and while it is open. Once
  // chosen it stays, disabled, until the goodbye starts.
  const endChosen = ending !== null && index <= ending.after && phase !== 'ended'
  const endOffered = live && open && phase !== 'idle'
  const checkInShown = checkIn && live && open && ending === null
  // Why a live show is silent, for the status line.
  let waitingLine = 'The host is getting the next song ready...'
  if (ending?.pending) waitingLine = 'The host is getting the goodbye ready...'
  else if (checkInShown) waitingLine = 'The show waits for you. Press "Still listening?" to go on.'
  else if (ask.state === 'failed') waitingLine = 'The next song is not ready.'
  let status: string
  if (!playerReady) status = 'Loading the YouTube player...'
  else if (phase === 'idle') status = 'Press Play to start the show.'
  // An endless show the listener left has no goodbye: it stops after its last song.
  else if (phase === 'ended' && open) status = 'The show stopped here. Press Play to hear it again.'
  else if (phase === 'ended') status = 'The show has ended. Press Play to hear it again.'
  else if (phase === 'blocked') status = 'The browser did not start the sound. Press Continue.'
  else if (phase === 'waiting') status = waitingLine
  else if (isVideo(current)) status = 'The song plays in the YouTube player.'
  else if (current?.kind === 'song') status = 'A song is playing.'
  else status = 'The host speaks.'

  return (
    <>
      {/* The YouTube player stays visible while the show plays: a policy of the embed. */}
      {hasVideos && <div className="video-box" ref={boxRef} />}
      <div className="player-controls">
        {/* While the show waits for its next part there is nothing to pause or to skip. */}
        <button
          type="button"
          className="primary"
          disabled={!playerReady || phase === 'waiting'}
          onClick={togglePlay}
        >
          {phase === 'playing' ? 'Pause' : 'Play'}
        </button>
        <button type="button" className="choice" disabled={!started} onClick={next}>
          Next
        </button>
        {phase === 'blocked' && (
          <button type="button" className="choice" onClick={resume}>
            Continue
          </button>
        )}
        {/* Shown once the automatic retry has failed too: the listener decides (D044). */}
        {phase === 'waiting' && ask.state === 'failed' && !checkInShown && !endChosen && (
          <button type="button" className="choice" onClick={askNext}>
            Try again
          </button>
        )}
        {checkInShown && (
          <button type="button" className="choice" onClick={stillListening}>
            Still listening?
          </button>
        )}
        {(endOffered || endChosen) && (
          <button type="button" className="choice" disabled={endChosen} onClick={endAfterSong}>
            {endChosen ? 'Ending after this song' : 'End after this song'}
          </button>
        )}
      </div>
      <p className="muted player-status" aria-live="polite">
        {status}
      </p>
      {/* Once the listener chose to end, a missing next part no longer matters. */}
      {ask.state === 'failed' && ending === null && (
        <p className="error player-note">
          The next part could not be made: {ask.message}
          {phase !== 'waiting' && <> The player tries again when this song ends.</>}
        </p>
      )}
      {endError !== null && (
        <p className="error player-note">The show could not be ended: {endError}</p>
      )}
      {checkInShown && phase !== 'waiting' && (
        <p className="muted player-note">
          Nobody has clicked for a while. The show stops after this song unless you press
          "Still listening?".
        </p>
      )}
      {/* Why the host closed the show by itself (D044). It is in show.json, so a replay
          shows it too. */}
      {show.end_note && <p className="muted player-note">{show.end_note}</p>}
      <Script
        segments={segments}
        tracks={tracksById}
        currentIndex={started ? index : -1}
        failed={failed}
      />
    </>
  )
}
