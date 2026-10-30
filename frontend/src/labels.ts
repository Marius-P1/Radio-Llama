import type { ShowSummary } from './api'

/** Plain-English labels for values the API returns as identifiers. */

export function styleLabel(style: string): string {
  return style.charAt(0).toUpperCase() + style.slice(1)
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
