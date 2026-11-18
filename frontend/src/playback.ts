import type { Segment, ShowResponse } from './api'

// What the segment player needs that does not need React: loading the YouTube IFrame
// API, starting the audio element, and a few questions about a show and its segments.

// How long the IFrame API gets to call back. A privacy extension can serve a stub that
// never does: the show then plays without the songs instead of staying disabled.
const API_TIMEOUT_MS = 10_000

let apiLoading: Promise<typeof YT> | null = null

/** Load https://www.youtube.com/iframe_api once per page and resolve when it is usable. */
export function loadYouTubeApi(): Promise<typeof YT> {
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
 * A file that fails to load rejects with NotSupportedError: the element's `error`
 * event deals with it.
 */
export function playAudio(audio: HTMLAudioElement, onBlocked: () => void) {
  audio.play().catch((error: unknown) => {
    const name = error instanceof DOMException ? error.name : ''
    if (name === 'AbortError' || name === 'NotSupportedError') return
    if (name === 'NotAllowedError') {
      onBlocked()
    } else if (!playFailureLogged) {
      playFailureLogged = true
      console.warn('The host segment did not start.', error)
    }
  })
}

/**
 * Why a request failed, as a sentence that ends with a full stop. When the network
 * fails, fetch rejects with a TypeError whose text is the browser's own ("Failed to
 * fetch"): the listener gets a plain sentence instead.
 */
export function failureText(error: unknown): string {
  if (error instanceof TypeError) return 'The server could not be reached.'
  const text = error instanceof Error ? error.message : String(error)
  return /[.!?]$/.test(text) ? text : `${text}.`
}

/**
 * An endless show the host has not closed: its last segment is not the goodbye (D044).
 * `=== true` because a show.json written before the endless show has no such field.
 */
export function isOpen(show: ShowResponse): boolean {
  return show.config.endless === true && show.segments.at(-1)?.kind !== 'outro'
}

/** Whether the segment plays in the YouTube player: a song that has no file of its own. */
export function isVideo(segment: Segment | undefined): boolean {
  return segment !== undefined && segment.kind === 'song' && !segment.audio_url
}

/** The YouTube video of segment `i`, null unless it is a song with a video. */
export function videoIdOf(show: ShowResponse, i: number): string | null {
  const segment = show.segments[i]
  if (segment === undefined || !isVideo(segment) || !segment.track_id) return null
  return show.tracks.find((track) => track.id === segment.track_id)?.video_id ?? null
}

/** The first video at or after segment `from`, null when no song is left. */
export function nextVideoId(show: ShowResponse, from: number): string | null {
  for (let i = from; i < show.segments.length; i++) {
    const videoId = videoIdOf(show, i)
    if (videoId !== null) return videoId
  }
  return null
}
