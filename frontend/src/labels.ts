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
