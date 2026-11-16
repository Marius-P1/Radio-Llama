import { useEffect, useState } from 'react'
import { createShow, getOptions, type CreateShowRequest, type Options } from './api'
import { energyLabel, languageLabel, styleLabel } from './labels'

type Language = 'en' | 'fr'
type Energy = NonNullable<CreateShowRequest['energy']>
type Source = CreateShowRequest['source']

// The API caps the wish at this length (WISH_MAX_CHARS).
const WISH_MAX_CHARS = 200

type Props = {
  onCreated: (id: string) => void
}

export function CreateShow({ onCreated }: Props) {
  const [options, setOptions] = useState<Options | null>(null)
  // The music source: the local catalog, Jamendo or YouTube when the backend has their key
  // (D029, D038).
  const [source, setSource] = useState<Source>('catalog')
  // The style, or '' on a source that offers none: YouTube takes the wish instead (D039).
  const [style, setStyle] = useState('')
  const [persona, setPersona] = useState('')
  const [language, setLanguage] = useState<Language>('en')
  // null is "any energy": the backend then samples every track of the style.
  const [energy, setEnergy] = useState<Energy | null>(null)
  // Free text that refines the song choice (D030). Optional on Jamendo and sent as null when
  // blank; the one required input on YouTube, which has no style (D039).
  const [wish, setWish] = useState('')
  // Off by default: the full songs play. A demo cuts each song to 30 seconds (D023).
  const [demo, setDemo] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    getOptions()
      .then((loaded) => {
        if (cancelled) return
        setOptions(loaded)
        const first = loaded.sources[0]
        setSource(first?.id ?? 'catalog')
        setStyle(first?.styles[0] ?? '')
        setPersona(loaded.personas.en?.[0]?.id ?? '')
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const personas = options?.personas[language] ?? []
  const selectedSource = options?.sources.find((s) => s.id === source)
  const styles = selectedSource?.styles ?? []
  // A source without styles (YouTube) needs the wish: it is the only thing to say (D039).
  const wishRequired = selectedSource !== undefined && styles.length === 0
  const canCreate = persona !== '' && (wishRequired ? wish.trim() !== '' : style !== '')

  const chooseSource = (next: Source) => {
    setSource(next)
    // Each source has its own styles: keep the style when both offer it, else take the
    // first. A source without styles gets none.
    const available = options?.sources.find((s) => s.id === next)?.styles ?? []
    if (!available.includes(style)) setStyle(available[0] ?? '')
  }

  const chooseLanguage = (next: Language) => {
    setLanguage(next)
    // Persona ids are the same in both languages, but fall back to the first one if not.
    const available = options?.personas[next] ?? []
    if (!available.some((p) => p.id === persona)) setPersona(available[0]?.id ?? '')
  }

  const submit = async () => {
    setBusy(true)
    setError(null)
    try {
      const body: CreateShowRequest = {
        persona,
        language,
        generator: 'pipeline',
        energy,
        source,
        wish: selectedSource?.wish && wish.trim() !== '' ? wish.trim() : null,
        demo,
      }
      // The style goes only when one is chosen: a source without styles refuses one (D039).
      if (style !== '') body.style = style
      const show = await createShow(body)
      onCreated(show.id)
    } catch (err) {
      setError((err as Error).message)
      setBusy(false)
    }
  }

  if (options === null) {
    return (
      <section className="card">
        <h2>Create a show</h2>
        {error !== null ? (
          <p className="error">Could not load the choices: {error}</p>
        ) : (
          <p className="muted">Loading the choices...</p>
        )}
      </section>
    )
  }

  return (
    <section className="card">
      <h2>Create a show</h2>

      {options.sources.length > 1 && (
        <fieldset disabled={busy}>
          <legend>Music from</legend>
          <div className="choices">
            {options.sources.map((s) => (
              <button
                key={s.id}
                type="button"
                className="choice"
                aria-pressed={s.id === source}
                onClick={() => chooseSource(s.id)}
              >
                {s.name}
              </button>
            ))}
          </div>
        </fieldset>
      )}

      {/* With no style to pick (YouTube), the wish comes first and is required (D039). */}
      {wishRequired ? (
        <fieldset disabled={busy}>
          <legend>What do you want to hear tonight?</legend>
          <label className="wish">
            <input
              type="text"
              value={wish}
              required
              maxLength={WISH_MAX_CHARS}
              placeholder="90s French rap, upbeat / slow songs for a long drive / 80s Italian disco"
              onChange={(event) => setWish(event.target.value)}
            />
          </label>
        </fieldset>
      ) : (
        <fieldset disabled={busy}>
          <legend>Music style</legend>
          <div className="choices">
            {styles.map((s) => (
              <button
                key={s}
                type="button"
                className="choice"
                aria-pressed={s === style}
                onClick={() => setStyle(s)}
              >
                {styleLabel(s)}
              </button>
            ))}
          </div>
        </fieldset>
      )}

      <fieldset disabled={busy}>
        <legend>Language</legend>
        <div className="choices">
          {options.languages.map((l) => (
            <button
              key={l}
              type="button"
              className="choice"
              aria-pressed={l === language}
              onClick={() => chooseLanguage(l as Language)}
            >
              {languageLabel(l)}
            </button>
          ))}
        </div>
      </fieldset>

      <fieldset disabled={busy}>
        <legend>Energy</legend>
        <div className="choices">
          {[null, ...options.energies].map((e) => (
            <button
              key={e ?? 'any'}
              type="button"
              className="choice"
              aria-pressed={e === energy}
              onClick={() => setEnergy(e as Energy | null)}
            >
              {energyLabel(e)}
            </button>
          ))}
        </div>
      </fieldset>

      {selectedSource?.wish && !wishRequired && (
        <fieldset disabled={busy}>
          <legend>Anything else?</legend>
          <label className="wish">
            <span className="muted">
              Optional. A mood, an instrument, a decade, an artist. The search follows it.
            </span>
            <input
              type="text"
              value={wish}
              maxLength={WISH_MAX_CHARS}
              placeholder="piano for a rainy sunday"
              onChange={(event) => setWish(event.target.value)}
            />
          </label>
        </fieldset>
      )}

      <fieldset disabled={busy}>
        <legend>Host</legend>
        <div className="choices">
          {personas.map((p) => (
            <button
              key={p.id}
              type="button"
              className="choice"
              aria-pressed={p.id === persona}
              onClick={() => setPersona(p.id)}
            >
              {p.name}
            </button>
          ))}
        </div>
      </fieldset>

      <fieldset disabled={busy}>
        <legend>Song length</legend>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={demo}
            onChange={(event) => setDemo(event.target.checked)}
          />
          Demo
          <span className="muted">30 second excerpts, a faster show</span>
        </label>
      </fieldset>

      {error !== null && <p className="error">{error}</p>}

      <div className="actions">
        <button
          type="button"
          className="primary"
          disabled={busy || !canCreate}
          onClick={submit}
        >
          {busy ? 'Starting...' : 'Create my show'}
        </button>
      </div>
    </section>
  )
}
