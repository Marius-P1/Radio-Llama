import { useState } from 'react'
import { REPLAY } from './api'
import { CreateShow } from './CreateShow'
import { PastShows } from './PastShows'
import { Player } from './Player'
import { Progress } from './Progress'

// `live` is true when the player opens from the create flow, false from Past shows. An
// endless show only grows in a live player: reopened later, it is a replay (D044).
type View =
  | { name: 'create' }
  | { name: 'progress'; id: string }
  | { name: 'player'; id: string; live: boolean }

const REPO_URL = 'https://github.com/Marius-P1/Radio-Llama'

// BASE_URL is "/" in dev and "/Radio-Llama/" in the replay build (D018).
const LOGO_URL = import.meta.env.BASE_URL + 'logo.png'

function App() {
  const [view, setView] = useState<View>({ name: 'create' })

  const goCreate = () => setView({ name: 'create' })
  const goLive = (id: string) => setView({ name: 'player', id, live: true })
  const goReplay = (id: string) => setView({ name: 'player', id, live: false })

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
          <PastShows onOpen={goReplay} />
        </>
      )}
      {view.name === 'progress' && <Progress id={view.id} onReady={goLive} onBack={goCreate} />}
      {view.name === 'player' && <Player id={view.id} live={view.live} onBack={goCreate} />}
      <footer>
        <p>
          Music credited per track: Creative Commons tracks from the catalog and Jamendo,
          videos from YouTube. Voice by ElevenLabs. Non-commercial demo.
        </p>
        <p>
          Videos play in the YouTube player.{' '}
          <a href="https://www.youtube.com/t/terms" target="_blank" rel="noreferrer">
            YouTube Terms of Service
          </a>
          ,{' '}
          <a href="https://policies.google.com/privacy" target="_blank" rel="noreferrer">
            Google Privacy Policy
          </a>
          .
        </p>
        <p>
          <a href={REPO_URL} target="_blank" rel="noreferrer">
            Source code
          </a>
        </p>
      </footer>
    </main>
  )
}

export default App
