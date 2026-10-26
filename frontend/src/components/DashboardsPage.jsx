import { useContext, useState } from 'react'
import InlineEdit from './InlineEdit.jsx'
import { AppContext } from '../lib/appContext.js'
import { dashboardsApi } from '../lib/dashboardsApi.js'
import { dashboardHref, go } from '../lib/router.js'

const dateFmt = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })

// Список дашбордов: создание, поиск, переименование, удаление (BI-13, BI-23, BI-24).
export default function DashboardsPage() {
  const { dashboards, dashboardsError, refreshDashboards, notify } = useContext(AppContext)
  const [query, setQuery] = useState('')
  const [title, setTitle] = useState('')
  const [editingId, setEditingId] = useState(null)
  const q = query.trim().toLowerCase()
  const list = q ? dashboards.filter((d) => `${d.title} ${d.description || ''}`.toLowerCase().includes(q)) : dashboards

  async function create(e) {
    e.preventDefault()
    try {
      const d = await dashboardsApi.create({ title: title.trim() || 'Новый дашборд', widgets: [] })
      setTitle('')
      await refreshDashboards()
      go(dashboardHref(d.id))
    } catch (err) {
      notify(`Не удалось создать дашборд: ${err.message}`)
    }
  }

  async function rename(id, newTitle) {
    setEditingId(null)
    try {
      await dashboardsApi.update(id, { title: newTitle })
      refreshDashboards()
    } catch (err) {
      notify(`Не удалось переименовать: ${err.message}`)
    }
  }

  async function remove(d) {
    if (!window.confirm(`Удалить дашборд «${d.title}»?`)) return
    try {
      await dashboardsApi.remove(d.id)
      refreshDashboards()
    } catch (err) {
      notify(`Не удалось удалить: ${err.message}`)
    }
  }

  return (
    <div className="page">
      <div className="page-head">
        <h2>Дашборды</h2>
        <form className="inline-form" onSubmit={create}>
          <input placeholder="Название нового дашборда" aria-label="Название нового дашборда" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={80} />
          <button type="submit" className="primary">
            Создать
          </button>
        </form>
      </div>
      {dashboards.length > 0 && (
        <input type="search" className="search page-search" placeholder="Поиск по дашбордам" aria-label="Поиск по дашбордам" value={query} onChange={(e) => setQuery(e.target.value)} />
      )}
      {dashboardsError && <p className="error-text">Не удалось загрузить дашборды: {dashboardsError}</p>}
      {!dashboardsError && dashboards.length === 0 && (
        <div className="empty">
          <p>Дашбордов пока нет.</p>
          <p className="muted">
            Задайте вопрос в <a href="#/chat">чате</a> и нажмите «+ На дашборд» под ответом — или напишите «Собери дашборд».
          </p>
        </div>
      )}
      <ul className="dash-cards">
        {list.map((d) => (
          <li key={d.id} className="dash-card">
            {editingId === d.id ? (
              <InlineEdit value={d.title} label="Название дашборда" onSave={(t) => rename(d.id, t)} onCancel={() => setEditingId(null)} />
            ) : (
              <a className="dash-card-title" href={dashboardHref(d.id)}>
                {d.title}
              </a>
            )}
            {d.description && <p className="muted small">{d.description}</p>}
            <p className="muted small">
              Виджетов: {d.widget_count} · обновлён {d.updated_at ? dateFmt.format(new Date(d.updated_at)) : '—'}
            </p>
            <div className="dash-card-actions">
              <button className="link" onClick={() => setEditingId(d.id)}>
                Переименовать
              </button>
              <button className="link danger" onClick={() => remove(d)}>
                Удалить
              </button>
            </div>
          </li>
        ))}
      </ul>
      {q && list.length === 0 && <p className="muted">Ничего не найдено</p>}
    </div>
  )
}
