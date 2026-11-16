import { useEffect, useRef, useState, type RefObject } from 'react'
import type { VoiceOption } from './api'
import { languageLabel } from './labels'

type Props = {
  voices: VoiceOption[]
  /** The show's language: its voices come first and its samples are the ones played. */
  language: string
  /** The voice in use, highlighted in the list. null when it is not one of the listed. */
  selectedId: string | null
  onSelect: (id: string) => void
  /** Put on the search box, so the form can send the keyboard focus there. */
  searchRef: RefObject<HTMLInputElement | null>
}

/** The sample to play: the one in the show's language when the voice has one (D040). */
function sampleUrl(voice: VoiceOption, language: string): string | null {
  return voice.previews?.[language] ?? voice.preview_url ?? null
}

/** What the sample button of a row does, for its tooltip and for screen readers. */
function sampleLabel(described: string, hasSample: boolean, playing: boolean): string {
  if (!hasSample) return `No sample for ${described}`
  return playing ? `Stop the sample of ${described}` : `Play a sample of ${described}`
}

/** A label of the voice list ("middle_aged") as words. */
function words(label: string): string {
  return label.replace(/_/g, ' ')
}

/**
 * Text ready to compare: lower case and without accents, so "elise" finds "Élise" and
 * "québécois" finds "quebecois". NFD writes "é" as "e" plus a combining accent, and the
 * range in the pattern is those combining accents.
 */
function plain(text: string): string {
  return text
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
}

/**
 * What a listener may type to find a voice by its native language: the language's name
 * in English and in French for the two station languages, the code for any other.
 */
function languageWords(language: string | null | undefined): string[] {
  if (language === 'en') return ['English', 'anglais']
  if (language === 'fr') return ['French', 'français']
  return language ? [language] : []
}

/**
 * Where the voice is from, for its first tag: the accent, or the native language when
 * there is no accent or it is "standard", which tells a reader nothing.
 */
function origin(voice: VoiceOption): string | null {
  if (voice.accent && voice.accent.toLowerCase() !== 'standard') return words(voice.accent)
  return voice.language ? languageLabel(voice.language) : null
}

/**
 * The list of voices the host can speak with (D040): a search box, gender pills and one
 * row per voice with a sample to hear and a button to pick it.
 */
export function VoicePicker({ voices, language, selectedId, onSelect, searchRef }: Props) {
  const [search, setSearch] = useState('')
  // null is "All".
  const [gender, setGender] = useState<string | null>(null)
  // The voice whose sample is playing, and the last sample that failed to play.
  const [playingId, setPlayingId] = useState<string | null>(null)
  const [sampleError, setSampleError] = useState<string | null>(null)
  // One audio element for every sample, so two samples never play together.
  const audioRef = useRef<HTMLAudioElement>(null)
  const listRef = useRef<HTMLUListElement>(null)

  // The sample stops when the picker leaves the page.
  useEffect(() => {
    const audio = audioRef.current
    return () => audio?.pause()
  }, [])

  // The highlighted voice can sit below the visible part of the list. When it changes
  // (the list just loaded, another style, another language order), its row is brought
  // into view. The list's own scroll position is moved, not scrollIntoView, which would
  // also scroll the page. Typing in the search box changes neither value, so it never
  // scrolls.
  useEffect(() => {
    const list = listRef.current
    const row = list?.querySelector('.voice-select[aria-pressed="true"]')
    if (!list || !row) return
    const rowBox = row.getBoundingClientRect()
    const listBox = list.getBoundingClientRect()
    if (rowBox.top < listBox.top) list.scrollTop -= listBox.top - rowBox.top
    else if (rowBox.bottom > listBox.bottom) list.scrollTop += rowBox.bottom - listBox.bottom
  }, [selectedId, language])

  const toggleSample = (voice: VoiceOption) => {
    const audio = audioRef.current
    const url = sampleUrl(voice, language)
    if (audio === null || url === null) return
    setSampleError(null)
    if (playingId === voice.id) {
      audio.pause()
      setPlayingId(null)
      return
    }
    // A new source stops the sample that was playing.
    audio.src = url
    setPlayingId(voice.id)
    audio.play().catch((err: Error) => {
      // Stopping a sample or starting another one before this one began aborts it:
      // that is not a failure.
      if (err.name === 'AbortError') return
      setPlayingId((current) => (current === voice.id ? null : current))
      setSampleError(`Could not play the sample of ${voice.name}.`)
    })
  }

  // The genders the voices carry, each once, in the order they appear.
  const genders = [
    ...new Set(voices.map((voice) => voice.gender).filter((g): g is string => !!g)),
  ]

  // The search looks in the name, the accent, the tone and the native language.
  const needle = plain(search.trim())
  const shown = voices.filter(
    (voice) =>
      (gender === null || voice.gender === gender) &&
      (needle === '' ||
        [voice.name, voice.accent, voice.descriptive, ...languageWords(voice.language)].some(
          (text) => text && plain(text).includes(needle),
        )),
  )
  // Voices made for the show's language first (ElevenLabs calls them verified). The
  // others stay in the list: the multilingual model speaks both languages with any voice.
  const verified = (voice: VoiceOption) => (voice.languages ?? []).includes(language)
  const ordered = [...shown.filter(verified), ...shown.filter((voice) => !verified(voice))]

  return (
    <div className="voice-picker">
      <input
        ref={searchRef}
        type="search"
        className="text-input"
        value={search}
        placeholder="Search a voice"
        aria-label="Search the voices by name, accent, tone or language"
        onChange={(event) => setSearch(event.target.value)}
      />

      {genders.length > 0 && (
        <div className="choices">
          {[null, ...genders].map((g) => (
            <button
              key={g ?? 'all'}
              type="button"
              className="choice small"
              aria-pressed={g === gender}
              onClick={() => setGender(g)}
            >
              {g === null ? 'All' : words(g)}
            </button>
          ))}
        </div>
      )}

      <p className="muted voice-hint">
        Voices made for {languageLabel(language)} come first. The others can speak it too.
      </p>

      {ordered.length === 0 ? (
        <p className="muted">No voice matches.</p>
      ) : (
        <ul className="voice-list" ref={listRef}>
          {ordered.map((voice) => {
            const playing = voice.id === playingId
            const hasSample = sampleUrl(voice, language) !== null
            const from = origin(voice)
            const tone = voice.descriptive ? words(voice.descriptive) : null
            // Two voices can share a name: the tags tell them apart for a screen reader.
            const described = [voice.name, from, tone].filter((part) => part).join(', ')
            const label = sampleLabel(described, hasSample, playing)
            return (
              <li key={voice.id} className="voice-row">
                <button
                  type="button"
                  className="voice-play"
                  disabled={!hasSample}
                  title={label}
                  aria-label={label}
                  onClick={() => toggleSample(voice)}
                >
                  {/* Drawn shapes: the font has no play or stop glyph. */}
                  <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
                    {playing ? (
                      <rect x="3" y="3" width="10" height="10" fill="currentColor" />
                    ) : (
                      <path d="M4 2 L14 8 L4 14 Z" fill="currentColor" />
                    )}
                  </svg>
                </button>
                <button
                  type="button"
                  className="voice-select"
                  aria-pressed={voice.id === selectedId}
                  onClick={() => onSelect(voice.id)}
                >
                  <span className="voice-name">{voice.name}</span>
                  {from && <span className="tag">{from}</span>}
                  {tone && <span className="tag">{tone}</span>}
                </button>
              </li>
            )
          })}
        </ul>
      )}

      {sampleError !== null && <p className="error">{sampleError}</p>}

      {/* No controls, so nothing is drawn: the row buttons drive it. */}
      <audio
        ref={audioRef}
        onEnded={() => setPlayingId(null)}
        onError={() => setPlayingId(null)}
      />
    </div>
  )
}
