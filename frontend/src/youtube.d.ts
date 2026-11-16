// The parts of the YouTube IFrame Player API that the sequenced player uses (D038).
// Typed here instead of adding @types/youtube: the app only loads a video, plays,
// pauses and listens for three events. https://developers.google.com/youtube/iframe_api_reference

declare namespace YT {
  const PlayerState: {
    readonly UNSTARTED: -1
    readonly ENDED: 0
    readonly PLAYING: 1
    readonly PAUSED: 2
    readonly BUFFERING: 3
    readonly CUED: 5
  }

  interface PlayerEvent {
    target: Player
  }

  interface OnStateChangeEvent extends PlayerEvent {
    data: number
  }

  // 2: bad video id. 5: HTML5 player error. 100: video not found or private.
  // 101 and 150: the uploader does not allow embedding.
  interface OnErrorEvent extends PlayerEvent {
    data: number
  }

  interface PlayerVars {
    playsinline?: 0 | 1
  }

  interface PlayerOptions {
    videoId?: string
    playerVars?: PlayerVars
    events?: {
      onReady?: (event: PlayerEvent) => void
      onStateChange?: (event: OnStateChangeEvent) => void
      onError?: (event: OnErrorEvent) => void
      // Raised when the browser refuses to start a video without a user gesture.
      onAutoplayBlocked?: (event: PlayerEvent) => void
    }
  }

  interface VideoSpec {
    videoId: string
    startSeconds?: number
    endSeconds?: number
  }

  // The constructor replaces `element` with the player's iframe.
  class Player {
    constructor(element: HTMLElement, options: PlayerOptions)
    loadVideoById(video: string | VideoSpec): void
    cueVideoById(video: string | VideoSpec): void
    playVideo(): void
    pauseVideo(): void
    destroy(): void
  }
}

interface Window {
  // Set by https://www.youtube.com/iframe_api, which then calls onYouTubeIframeAPIReady.
  YT?: typeof YT
  onYouTubeIframeAPIReady?: () => void
}
