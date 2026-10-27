import { useCallback, useEffect, useMemo, useState } from 'react'
import Chat from './components/Chat.jsx'
import Sidebar from './components/Sidebar.jsx'
import DashboardsPage from './components/DashboardsPage.jsx'
import DashboardPage from './components/DashboardPage.jsx'
import ThemeSelect from './components/ThemeSelect.jsx'
import DataPage from './components/DataPage.jsx'
import { queriesApi } from './lib/queriesApi.js'
import { AppContext } from './lib/appContext.js'
import { useConversations } from './lib/store.js'
import { dashboardsApi } from './lib/dashboardsApi.js'
import { dashboardHref, go, useRoute } from './lib/router.js'
import { isMock, listDatasources } from './lib/api.js'

export default function App() {
  const route = useRoute()
  const [menuOpen, setMenuOpen] = useState(false)
  const [dashboards, setDashboards] = useState([])
  const [dashboardsError, setDashboardsError] = useState(null)
  const [toast, setToast] = useState(null)
  const [datasources, setDatasources] = useState([])
  const [queries, setQueries] = useState([])

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

  const refreshDatasources = useCallback(async () => {
    try {
      setDatasources(await listDatasources())
    } catch {
      setDatasources([]) // без списка чат работает со встроенным источником
    }
  }, [])

  useEffect(() => {
    refreshDatasources()
  }, [refreshDatasources])

  const refreshQueries = useCallback(async () => {
    try {
      setQueries(await queriesApi.list())
    } catch {
      setQueries([])
    }
  }, [])

  useEffect(() => {
    refreshQueries()
  }, [refreshQueries])

  useEffect(() => {
    const onFocus = () => {
      refreshDashboards()
      refreshDatasources()
      refreshQueries()
    }
    window.addEventListener('focus', onFocus)
    return () => window.removeEventListener('focus', onFocus)
  }, [refreshDashboards, refreshDatasources, refreshQueries])

  const notify = useCallback((text, href) => setToast({ text, href, key: Date.now() }), [])

  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(null), 5000)
    return () => clearTimeout(t)
  }, [toast])

  const {
    conversations,
    active,
    loadingIds,
    progress,
    create,
    select,
    remove,
    send,
    setView,
    rename,
    buildDashboard,
    setDatasource,
    startConversation,
    clearTable,
    runSavedQuery,
  } = useConversations({
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

  function onChangeDatasource(id, datasourceId) {
    const startedNew = setDatasource(id, datasourceId)
    if (startedNew) {
      const name = datasources.find((d) => d.id === datasourceId)?.name || datasourceId
      notify(`Начат новый диалог с источником «${name}» — контекст прошлого источника не переносится`)
    }
  }

  function onAskTable(tableName, displayName) {
    go('#/chat')
    startConversation({ datasourceId: 'raw', tableName, title: `Таблица: ${displayName || tableName}` }, 'Опиши данные')
  }

  function onRunQuery(query) {
    go('#/chat')
    setMenuOpen(false)
    runSavedQuery(active.id, query)
  }

  async function onRenameQuery(id, title) {
    try {
      await queriesApi.update(id, { title })
      refreshQueries()
    } catch (e) {
      notify(`Не удалось переименовать запрос: ${e.message}`)
    }
  }

  async function onDeleteQuery(id) {
    try {
      await queriesApi.remove(id)
      refreshQueries()
    } catch (e) {
      notify(`Не удалось удалить запрос: ${e.message}`)
    }
  }

  const ctx = useMemo(
    () => ({ dashboards, dashboardsError, refreshDashboards, notify, datasources, refreshDatasources, queries, refreshQueries }),
    [dashboards, dashboardsError, refreshDashboards, notify, datasources, refreshDatasources, queries, refreshQueries]
  )

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
            <a href="#/dashboards" className={route.page === 'dashboards' || route.page === 'dashboard' ? 'nav-on' : ''}>
              Дашборды
            </a>
            <a href="#/data" className={route.page === 'data' ? 'nav-on' : ''}>
              Данные
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
            queries={queries}
            onRunQuery={onRunQuery}
            onRenameQuery={onRenameQuery}
            onDeleteQuery={onDeleteQuery}
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
                progress={progress[active.id]}
                onSend={send}
                onChangeView={setView}
                onBuildDashboard={onBuildDashboard}
                onChangeDatasource={onChangeDatasource}
                onClearTable={clearTable}
              />
            )}
            {route.page === 'data' && <DataPage onAskTable={onAskTable} />}
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
