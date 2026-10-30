import type { CreateShowRequest, Options, ShowResponse, ShowSummary } from './types'

// Static files under <site>/shows/, written by `radio-llama export-replay`.
// BASE_URL already ends with a slash ("/" or "/Radio-Llama/").
const BASE_URL = import.meta.env.BASE_URL

// Show ids come from the backend (`new_show_id`): date, time, six hex chars.
const SHOW_ID = /^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$/

/** The backend `Show` model as written to `show.json`. */
type ShowFile = Omit<ShowResponse, 'progress' | 'audio_url'> & {
  audio_file?: string | null
}

function checkId(id: string): void {
  if (!SHOW_ID.test(id)) throw new Error('Invalid show id.')
}

async function fetchJson<T>(url: string): Promise<T> {
  const response = await fetch(url)
  if (!response.ok) throw new Error(`Request failed (${response.status} ${response.statusText})`)
  return (await response.json()) as T
}

export async function listShows(): Promise<ShowSummary[]> {
  const response = await fetch(`${BASE_URL}shows/index.json`)
  // A replay build with no exported shows has no index: show an empty list, not an error.
  if (response.status === 404) return []
  if (!response.ok) throw new Error(`Request failed (${response.status} ${response.statusText})`)
  return (await response.json()) as ShowSummary[]
}

export async function getShow(id: string): Promise<ShowResponse> {
  checkId(id)
  const show = await fetchJson<ShowFile>(`${BASE_URL}shows/${id}/show.json`)
  return {
    id: show.id,
    status: show.status,
    created_at: show.created_at,
    config: show.config,
    error: show.error ?? null,
    progress: null,
    segments: show.segments,
    tracks: show.tracks,
    audio_url: audioUrl(id),
  }
}

export function audioUrl(id: string): string {
  checkId(id)
  return `${BASE_URL}shows/${id}/show.mp3`
}

export async function getOptions(): Promise<Options> {
  throw new Error('Not available on the replay page.')
}

export async function createShow(_body: CreateShowRequest): Promise<ShowSummary> {
  throw new Error('Not available on the replay page.')
}
