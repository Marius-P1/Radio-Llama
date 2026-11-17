import createClient from 'openapi-fetch'
import type { paths } from './schema'
import type { CreateShowRequest, Options, ShowResponse, ShowSummary, VoiceOption } from './types'

// Relative URLs on purpose: Vite proxies /api in dev, the backend serves the built files in production.
export const api = createClient<paths>({ baseUrl: '' })

/** Turn a failed response into an Error carrying the server's `detail`. */
function toError(error: unknown, response: Response): Error {
  const detail = (error as { detail?: unknown } | undefined)?.detail
  if (typeof detail === 'string') return new Error(detail)
  if (Array.isArray(detail)) {
    // FastAPI validation errors: a list of {loc, msg, type}.
    const messages = detail
      .map((item) => (item as { msg?: unknown }).msg)
      .filter((msg): msg is string => typeof msg === 'string')
    if (messages.length > 0) return new Error(messages.join(' '))
  }
  return new Error(`Request failed (${response.status} ${response.statusText})`)
}

export async function getOptions(): Promise<Options> {
  const { data, error, response } = await api.GET('/api/v1/options')
  if (data === undefined) throw toError(error, response)
  return data
}

/** The voices the host can speak with (D040). Fails when the backend cannot list them. */
export async function getVoices(): Promise<VoiceOption[]> {
  const { data, error, response } = await api.GET('/api/v1/voices')
  if (data === undefined) throw toError(error, response)
  return data
}

export async function createShow(body: CreateShowRequest): Promise<ShowSummary> {
  const { data, error, response } = await api.POST('/api/v1/shows', { body })
  if (data === undefined) throw toError(error, response)
  return data
}

export async function getShow(id: string): Promise<ShowResponse> {
  const { data, error, response } = await api.GET('/api/v1/shows/{show_id}', {
    params: { path: { show_id: id } },
  })
  if (data === undefined) throw toError(error, response)
  return data
}

/**
 * Ask for the next break and song of an endless show (D044). `known` is the number of
 * segments the player has. The request waits until the part is made and returns the
 * whole show: longer, or closed by the host when it has nothing left to play.
 */
export async function nextPart(id: string, known: number): Promise<ShowResponse> {
  const { data, error, response } = await api.POST('/api/v1/shows/{show_id}/next', {
    params: { path: { show_id: id } },
    body: { known },
  })
  if (data === undefined) throw toError(error, response)
  return data
}

export async function listShows(): Promise<ShowSummary[]> {
  const { data, error, response } = await api.GET('/api/v1/shows')
  if (data === undefined) throw toError(error, response)
  return data
}

export function audioUrl(id: string): string {
  return `/api/v1/shows/${id}/audio`
}
