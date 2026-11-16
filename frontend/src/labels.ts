import type { ShowSummary, Track } from './api'

/** Plain-English labels for values the API returns as identifiers. */

function capitalize(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1)
}

export function styleLabel(style: string): string {
  return capitalize(style)
}

/**
 * What the show is about: its style, or its wish in quotes when it has no style (a YouTube
 * show, D039), or null when it has neither.
 */
export function themeLabel(config: ShowSummary['config']): string | null {
  if (config.style) return styleLabel(config.style)
  if (config.wish) return `"${config.wish}"`
  return null
}

export function energyLabel(energy: string | null | undefined): string {
  if (energy === null || energy === undefined) return 'Any'
  return capitalize(energy)
}

/** The persona id ("witty"), capitalised. The display name would need the options call. */
export function personaLabel(persona: string): string {
  return capitalize(persona)
}

/**
 * A voice name for a title or a sentence: the part before " - ". ElevenLabs names read
 * "Mark - Casual, Relaxed and Light"; the voice list shows them in full. A name with
 * nothing before the " - " is kept whole.
 */
export function shortVoiceName(name: string): string {
  return name.split(' - ')[0].trim() || name
}

/** "Calm host", or "Calm host, voice Gabriel" when the listener picked a voice (D040). */
export function hostLabel(config: ShowSummary['config']): string {
  const host = `${personaLabel(config.persona)} host`
  return config.voice_name ? `${host}, voice ${shortVoiceName(config.voice_name)}` : host
}

/** The music source as a short tag. The catalog is the default and gets no tag. */
export function sourceTag(source: ShowSummary['config']['source']): string | null {
  if (source === 'jamendo') return 'Jamendo'
  if (source === 'youtube') return 'YouTube'
  return null
}

/** The title to show and speak: the spoken title when the catalog sets one. */
export function displayTitle(track: Track): string {
  return track.spoken_title ?? track.title
}

export function languageLabel(language: string): string {
  if (language === 'en') return 'English'
  if (language === 'fr') return 'French'
  return language
}

export function statusLabel(status: ShowSummary['status']): string {
  switch (status) {
    case 'pending':
      return 'Waiting'
    case 'generating':
      return 'In progress'
    case 'ready':
      return 'Ready'
    case 'failed':
      return 'Failed'
  }
}

export function dateLabel(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

/** A fact's source as a short link label: the service's name, else the site's host name. */
export function factSourceLabel(sourceType: string | null | undefined, url: string): string {
  if (sourceType === 'wikipedia') return 'Wikipedia'
  if (sourceType === 'musicbrainz') return 'MusicBrainz'
  if (sourceType === 'jamendo_artist_page') return 'Jamendo'
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return url
  }
}
