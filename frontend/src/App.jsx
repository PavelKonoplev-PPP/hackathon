import { useCallback, useEffect, useMemo, useState } from 'react'
import Chat from './components/Chat.jsx'
import Sidebar from './components/Sidebar.jsx'
import DashboardsPage from './components/DashboardsPage.jsx'
import DashboardPage from './components/DashboardPage.jsx'
import ThemeSelect from './components/ThemeSelect.jsx'
import { AppContext } from './lib/appContext.js'
import { useConversations } from './lib/store.js'
import { dashboardsApi } from './lib/dashboardsApi.js'
import { dashboardHref, go, useRoute } from './lib/router.js'
import { isMock } from './lib/api.js'

export default function App() {
  const route = useRoute()
  const [menuOpen, setMenuOpen] = useState(false)
  const [dashboards, setDashboards] = useState([])
  const [dashboardsError, setDashboardsError] = useState(null)
  const [toast, setToast] = useState(null)

  const refreshDashboards = useCallback(async () => {
    try {
      const list = await dashboardsApi.list()
      setDashboards([...list].sort((a, b) => String(b.updated_at || '').localeCompare(String(a.updated_at || ''))))
      setDashboardsError(null)
    } catch (e) {
      setDashboardsError(e.message)
    }
  }, [])

  // Список обновляем при входе в раздел дашбордов и при возврате на вкладку:
  // если бэкенд был недоступен при загрузке страницы, список подтянется позже.
  useEffect(() => {
    refreshDashboards()
  }, [refreshDashboards, route.page])

  useEffect(() => {
    const onFocus = () => refreshDashboards()
    window.addEventListener('focus', onFocus)
    return () => window.removeEventListener('focus', onFocus)
  }, [refreshDashboards])

  const notify = useCallback((text, href) => setToast({ text, href, key: Date.now() }), [])

  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(null), 5000)
    return () => clearTimeout(t)
  }, [toast])

  const { conversations, active, loadingIds, create, select, remove, send, setView, rename, buildDashboard } = useConversations({
    onDashboardCreated: refreshDashboards,
  })

  async function onBuildDashboard(id) {
    try {
      const d = await buildDashboard(id)
      if (d) {
        notify(`Собран дашборд «${d.title}»`, dashboardHref(d.id))
        go(dashboardHref(d.id))
      }
    } catch (e) {
      notify(`Не удалось собрать дашборд: ${e.message}`)
    }
  }

  const ctx = useMemo(() => ({ dashboards, dashboardsError, refreshDashboards, notify }), [dashboards, dashboardsError, refreshDashboards, notify])

  return (
    <AppContext.Provider value={ctx}>
      <div className="app">
        <header className="topbar">
          <button className="ghost menu-btn" onClick={() => setMenuOpen(true)} aria-label="Открыть список диалогов и дашбордов">
            Меню
          </button>
          <h1>
            <a href="#/chat">AI-аналитик</a>
          </h1>
          <nav className="nav" aria-label="Разделы">
            <a href="#/chat" className={route.page === 'chat' ? 'nav-on' : ''}>
              Чат
            </a>
            <a href="#/dashboards" className={route.page !== 'chat' ? 'nav-on' : ''}>
              Дашборды
            </a>
          </nav>
          <span className="spacer" />
          {isMock && <span className="badge">Демо-режим: ответы-заглушки</span>}
          <ThemeSelect />
        </header>
        <div className="layout">
          <Sidebar
            conversations={conversations}
            activeId={active.id}
            loadingIds={loadingIds}
            dashboards={dashboards}
            dashboardsError={dashboardsError}
            onRetryDashboards={refreshDashboards}
            route={route}
            open={menuOpen}
            onClose={() => setMenuOpen(false)}
            onCreate={() => {
              create()
              go('#/chat')
              setMenuOpen(false)
            }}
            onSelect={(id) => {
              select(id)
              go('#/chat')
              setMenuOpen(false)
            }}
            onDelete={remove}
            onRename={rename}
          />
          <main>
            {route.page === 'chat' && (
              <Chat
                conversation={active}
                loading={loadingIds.includes(active.id)}
                onSend={send}
                onChangeView={setView}
                onBuildDashboard={onBuildDashboard}
              />
            )}
            {route.page === 'dashboards' && <DashboardsPage />}
            {route.page === 'dashboard' && <DashboardPage key={route.id} id={route.id} />}
          </main>
        </div>
        {toast && (
          <div className="toast" role="status" key={toast.key}>
            <span>{toast.text}</span>
            {toast.href && (
              <a href={toast.href} onClick={() => setToast(null)}>
                Открыть
              </a>
            )}
            <button className="dialog-act" aria-label="Закрыть уведомление" onClick={() => setToast(null)}>
              ×
            </button>
          </div>
        )}
      </div>
    </AppContext.Provider>
  )
}
