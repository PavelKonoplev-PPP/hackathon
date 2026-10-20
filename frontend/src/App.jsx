import { useState } from 'react'
import Chat from './components/Chat.jsx'
import { isMock } from './lib/api.js'

export default function App() {
  const [resetKey, setResetKey] = useState(0)

  return (
    <div className="app">
      <header className="topbar">
        <h1>AI-аналитик</h1>
        {isMock && <span className="badge">Демо-режим: ответы-заглушки</span>}
        <button className="ghost" onClick={() => setResetKey((k) => k + 1)}>
          Новый диалог
        </button>
      </header>
      <main>
        <Chat resetKey={resetKey} />
      </main>
    </div>
  )
}
