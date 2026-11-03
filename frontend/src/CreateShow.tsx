import { useEffect, useState } from 'react'
import { createShow, getOptions, type CreateShowRequest, type Options } from './api'
import { energyLabel, languageLabel, styleLabel } from './labels'

type Language = 'en' | 'fr'
type Energy = NonNullable<CreateShowRequest['energy']>

type Props = {
  onCreated: (id: string) => void
}

export function CreateShow({ onCreated }: Props) {
  const [options, setOptions] = useState<Options | null>(null)
  const [style, setStyle] = useState('')
  const [persona, setPersona] = useState('')
  const [language, setLanguage] = useState<Language>('en')
  // null is "any energy": the backend then samples every track of the style.
  const [energy, setEnergy] = useState<Energy | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    getOptions()
      .then((loaded) => {
        if (cancelled) return
        setOptions(loaded)
        setStyle(loaded.styles[0] ?? '')
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
      const show = await createShow({ style, persona, language, generator: 'pipeline', energy })
      onCreated(show.id)
    } catch (err) {
      setError((err as Error).message)
      setBusy(false)
    }
  }

  if (options === null) {
    return (
      <section>
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
    <section>
      <h2>Create a show</h2>

      <fieldset disabled={busy}>
        <legend>Music style</legend>
        <div className="choices">
          {options.styles.map((s) => (
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

      <fieldset disabled={busy}>
        <legend>Host</legend>
        <div className="choices personas">
          {personas.map((p) => (
            <button
              key={p.id}
              type="button"
              className="choice persona"
              aria-pressed={p.id === persona}
              onClick={() => setPersona(p.id)}
            >
              <strong>{p.name}</strong>
            </button>
          ))}
        </div>
      </fieldset>

      {error !== null && <p className="error">{error}</p>}

      <button
        type="button"
        className="primary"
        disabled={busy || style === '' || persona === ''}
        onClick={submit}
      >
        {busy ? 'Starting...' : 'Create my show'}
      </button>
    </section>
  )
}
