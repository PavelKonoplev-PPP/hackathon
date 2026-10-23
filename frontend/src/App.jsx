import { useState } from 'react'
import Chat from './components/Chat.jsx'
import Sidebar from './components/Sidebar.jsx'
import { useConversations } from './lib/store.js'
import { isMock } from './lib/api.js'

export default function App() {
  const { conversations, active, loadingIds, create, select, remove, send } = useConversations()
  const [menuOpen, setMenuOpen] = useState(false)

  return (
    <div className="app">
      <header className="topbar">
        <button className="ghost menu-btn" onClick={() => setMenuOpen(true)} aria-label="Открыть список диалогов">
          Диалоги
        </button>
        <h1>AI-аналитик</h1>
        {isMock && <span className="badge">Демо-режим: ответы-заглушки</span>}
      </header>
      <div className="layout">
        <Sidebar
          conversations={conversations}
          activeId={active.id}
          loadingIds={loadingIds}
          open={menuOpen}
          onClose={() => setMenuOpen(false)}
          onCreate={() => {
            create()
            setMenuOpen(false)
          }}
          onSelect={(id) => {
            select(id)
            setMenuOpen(false)
          }}
          onDelete={remove}
        />
        <main>
          <Chat conversation={active} loading={loadingIds.includes(active.id)} onSend={send} />
        </main>
      </div>
    </div>
  )
}
