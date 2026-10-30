import { useState } from 'react'
import { REPLAY } from './api'
import { CreateShow } from './CreateShow'
import { PastShows } from './PastShows'
import { Player } from './Player'
import { Progress } from './Progress'

type View = { name: 'create' } | { name: 'progress'; id: string } | { name: 'player'; id: string }

const REPO_URL = 'https://github.com/Marius-P1/Radio-Llama'

function App() {
  const [view, setView] = useState<View>({ name: 'create' })

  const goCreate = () => setView({ name: 'create' })
  const goPlayer = (id: string) => setView({ name: 'player', id })

  return (
    <main>
      <header>
        <h1>Radio Llama</h1>
        <p className="tagline">A short radio show, written and hosted for you.</p>
      </header>
      {view.name === 'create' && (
        <>
          {REPLAY ? (
            <p className="intro">
              These shows were generated once and recorded here. Pick one to listen.
            </p>
          ) : (
            <CreateShow onCreated={(id) => setView({ name: 'progress', id })} />
          )}
          <PastShows onOpen={goPlayer} />
        </>
      )}
      {view.name === 'progress' && <Progress id={view.id} onReady={goPlayer} onBack={goCreate} />}
      {view.name === 'player' && <Player id={view.id} onBack={goCreate} />}
      <footer className="site-footer">
        {REPLAY && (
          <p className="muted">
            Shows published under CC BY-SA 4.0. Each show lists its music credits. Voice by
            ElevenLabs.
          </p>
        )}
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
