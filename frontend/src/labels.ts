import type { ShowSummary } from './api'

/** Plain-English labels for values the API returns as identifiers. */

function capitalize(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1)
}

export function styleLabel(style: string): string {
  return capitalize(style)
}

export function energyLabel(energy: string | null | undefined): string {
  if (energy === null || energy === undefined) return 'Any'
  return capitalize(energy)
}

/** The persona id ("witty"), capitalised. The display name would need the options call. */
export function personaLabel(persona: string): string {
  return capitalize(persona)
}

/** The music source as a short tag. The catalog is the default and gets no tag. */
export function sourceTag(source: ShowSummary['config']['source']): string | null {
  if (source === 'jamendo') return 'Jamendo'
  return null
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
