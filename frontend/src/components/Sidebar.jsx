import { useState } from 'react'
import InlineEdit from './InlineEdit.jsx'
import { dashboardHref } from '../lib/router.js'

function DialogItem({ conv, active, busy, onSelect, onDelete, onRename }) {
  const [editing, setEditing] = useState(false)

  if (editing) {
    return (
      <li>
        <div className="dialog dialog-active">
          <InlineEdit
            value={conv.title}
            label="Название диалога"
            onSave={(t) => {
              onRename(conv.id, t)
              setEditing(false)
            }}
            onCancel={() => setEditing(false)}
          />
        </div>
      </li>
    )
  }

  return (
    <li>
      <div
        className={`dialog ${active ? 'dialog-active' : ''}`}
        role="button"
        tabIndex={0}
        onClick={() => onSelect(conv.id)}
        onDoubleClick={() => setEditing(true)}
        onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && onSelect(conv.id)}
      >
        <span className="dialog-title">{conv.title}</span>
        {busy && <span className="dialog-busy" aria-label="Идёт ответ" />}
        <button
          className="dialog-act"
          aria-label={`Переименовать диалог ${conv.title}`}
          title="Переименовать"
          onClick={(e) => {
            e.stopPropagation()
            setEditing(true)
          }}
        >
          ✎
        </button>
        <button
          className="dialog-act dialog-delete"
          aria-label={`Удалить диалог ${conv.title}`}
          title="Удалить"
          onClick={(e) => {
            e.stopPropagation()
            if (window.confirm(`Удалить диалог «${conv.title}»?`)) onDelete(conv.id)
          }}
        >
          ×
        </button>
      </div>
    </li>
  )
}

const matches = (text, q) => (text || '').toLowerCase().includes(q)

// Боковая панель: поиск (BI-24), диалоги с переименованием (BI-21, BI-23) и дашборды.
export default function Sidebar({ conversations, activeId, loadingIds, dashboards, dashboardsError, onRetryDashboards, route, open, onCreate, onSelect, onDelete, onRename, onClose }) {
  const [query, setQuery] = useState('')
  const q = query.trim().toLowerCase()
  const dialogs = q ? conversations.filter((c) => matches(c.title, q) || c.messages.some((m) => matches(m.text, q))) : conversations
  const dashes = q ? dashboards.filter((d) => matches(d.title, q) || matches(d.description, q)) : dashboards
  const inChat = route.page === 'chat'

  return (
    <>
      {open && <div className="scrim" onClick={onClose} />}
      <aside className={`sidebar ${open ? 'sidebar-open' : ''}`} aria-label="Диалоги и дашборды">
        <input
          type="search"
          className="search"
          placeholder="Поиск по диалогам и дашбордам"
          aria-label="Поиск по диалогам и дашбордам"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <button className="new-chat" onClick={onCreate}>
          + Новый диалог
        </button>

        <p className="side-section">Диалоги</p>
        <ul className="dialogs">
          {dialogs.map((c) => (
            <DialogItem
              key={c.id}
              conv={c}
              active={inChat && c.id === activeId}
              busy={loadingIds.includes(c.id)}
              onSelect={onSelect}
              onDelete={onDelete}
              onRename={onRename}
            />
          ))}
        </ul>
        {q && dialogs.length === 0 && <p className="muted small">Диалогов не найдено</p>}

        <p className="side-section">
          <a href="#/dashboards" onClick={onClose}>
            Дашборды
          </a>
        </p>
        <ul className="dialogs">
          {dashes.map((d) => (
            <li key={d.id}>
              <a
                className={`dialog ${route.page === 'dashboard' && route.id === d.id ? 'dialog-active' : ''}`}
                href={dashboardHref(d.id)}
                onClick={onClose}
              >
                <span className="dialog-title">{d.title}</span>
                <span className="count" title="Виджетов">
                  {d.widget_count}
                </span>
              </a>
            </li>
          ))}
        </ul>
        {q && dashes.length === 0 && dashboards.length > 0 && <p className="muted small">Дашбордов не найдено</p>}
        {dashboardsError && (
          <p className="error-text small">
            Не удалось загрузить дашборды.{' '}
            <button className="link" onClick={onRetryDashboards}>
              Повторить
            </button>
          </p>
        )}
        {!dashboardsError && dashboards.length === 0 && <p className="muted small">Пока нет. Добавьте график из чата кнопкой «+ На дашборд».</p>}
      </aside>
    </>
  )
}
