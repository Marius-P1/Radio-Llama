import { useState } from 'react'
import { REPLAY } from './api'
import { CreateShow } from './CreateShow'
import { PastShows } from './PastShows'
import { Player } from './Player'
import { Progress } from './Progress'

type View = { name: 'create' } | { name: 'progress'; id: string } | { name: 'player'; id: string }

const REPO_URL = 'https://github.com/Marius-P1/Radio-Llama'

// BASE_URL is "/" in dev and "/Radio-Llama/" in the replay build (D018).
const LOGO_URL = import.meta.env.BASE_URL + 'logo.png'

function App() {
  const [view, setView] = useState<View>({ name: 'create' })

  const goCreate = () => setView({ name: 'create' })
  const goPlayer = (id: string) => setView({ name: 'player', id })

  return (
    <main>
      <header className="site-header">
        <img src={LOGO_URL} alt="" width={48} height={48} />
        <div>
          <h1 className="brand">RADIOLLAMA</h1>
          <p className="tagline">A short radio show, written and hosted for you.</p>
        </div>
      </header>
      {view.name === 'create' && (
        <>
          {REPLAY ? (
            <section className="card">
              <p className="intro">
                These shows were generated once and recorded here. Pick one to listen.
              </p>
            </section>
          ) : (
            <CreateShow onCreated={(id) => setView({ name: 'progress', id })} />
          )}
          <PastShows onOpen={goPlayer} />
        </>
      )}
      {view.name === 'progress' && <Progress id={view.id} onReady={goPlayer} onBack={goCreate} />}
      {view.name === 'player' && <Player id={view.id} onBack={goCreate} />}
      <footer>
        <p className="muted">
          Music under Creative Commons licenses, credited per track. Voice by ElevenLabs.
          Non-commercial demo.
        </p>
        <p className="muted">
          <a href={REPO_URL} target="_blank" rel="noreferrer">
            Source code
          </a>
        </p>
      </footer>
    </main>
  )
}

export default App
