import { useState } from 'react'
import { CreateShow } from './CreateShow'
import { PastShows } from './PastShows'
import { Player } from './Player'
import { Progress } from './Progress'

type View = { name: 'create' } | { name: 'progress'; id: string } | { name: 'player'; id: string }

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
          <CreateShow onCreated={(id) => setView({ name: 'progress', id })} />
          <PastShows onOpen={goPlayer} />
        </>
      )}
      {view.name === 'progress' && <Progress id={view.id} onReady={goPlayer} onBack={goCreate} />}
      {view.name === 'player' && <Player id={view.id} onBack={goCreate} />}
    </main>
  )
}

export default App
