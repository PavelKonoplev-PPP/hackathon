import { useContext, useEffect, useMemo, useRef, useState } from 'react'
import DashboardWidget from './DashboardWidget.jsx'
import InlineEdit from './InlineEdit.jsx'
import PeriodFilter from './PeriodFilter.jsx'
import { AppContext } from '../lib/appContext.js'
import { dashboardsApi } from '../lib/dashboardsApi.js'
import { deriveTable } from '../lib/chartViews.js'
import { downloadXlsx } from '../lib/export.js'
import { applyPeriod, isPeriodAware, periodBounds, usePeriod } from '../lib/period.js'
import { go } from '../lib/router.js'

// Дашборд из нескольких виджетов (BI-13) с общим периодом (BI-34) и сохранением макета (BI-18).
export default function DashboardPage({ id }) {
  const { refreshDashboards, notify } = useContext(AppContext)
  const [dash, setDash] = useState(null)
  const [widgets, setWidgets] = useState([])
  const [error, setError] = useState(null)
  const [editingTitle, setEditingTitle] = useState(false)
  const [dragId, setDragId] = useState(null)
  const [overId, setOverId] = useState(null)
  const [period, setPeriod] = usePeriod(id)
  const gridRef = useRef(null)
  const widgetsRef = useRef(widgets)
  widgetsRef.current = widgets

  useEffect(() => {
    let alive = true
    setDash(null)
    setError(null)
    dashboardsApi
      .get(id)
      .then((d) => {
        if (!alive) return
        setDash(d)
        setWidgets(d.widgets || [])
      })
      .catch((e) => alive && setError(e.message))
    return () => {
      alive = false
    }
  }, [id])

  async function save(patch) {
    try {
      await dashboardsApi.update(id, patch)
      refreshDashboards()
    } catch (e) {
      notify(`Не удалось сохранить дашборд: ${e.message}`)
    }
  }

  const persistWidgets = (next) => {
    setWidgets(next)
    save({ widgets: next })
  }

  const patchWidget = (wid, patch, commit = false) => {
    const next = widgetsRef.current.map((w) => (w.id === wid ? { ...w, ...patch } : w))
    widgetsRef.current = next
    if (commit) persistWidgets(next)
    else setWidgets(next)
  }

  const moveTo = (fromId, toId) => {
    const list = [...widgetsRef.current]
    const from = list.findIndex((w) => w.id === fromId)
    const to = list.findIndex((w) => w.id === toId)
    if (from < 0 || to < 0 || from === to) return
    const [item] = list.splice(from, 1)
    list.splice(to, 0, item)
    persistWidgets(list)
  }

  const moveBy = (wid, delta) => {
    const list = widgetsRef.current
    const i = list.findIndex((w) => w.id === wid)
    const j = i + delta
    if (i < 0 || j < 0 || j >= list.length) return
    moveTo(wid, list[j].id)
  }

  const bounds = useMemo(() => periodBounds(widgets), [widgets])
  const activePeriod = bounds && period && (period.from || period.to) ? period : null
  const shown = useMemo(() => widgets.map((w) => applyPeriod(w, activePeriod)), [widgets, activePeriod])

  function exportAll() {
    const sheets = shown
      .map((w) => ({ name: w.title, table: w.table || (w.chart ? deriveTable(w.chart) : null) }))
      .filter((s) => s.table)
    if (!sheets.length) return notify('На дашборде нет таблиц и графиков для выгрузки')
    downloadXlsx(sheets, dash.title)
  }

  async function removeDashboard() {
    if (!window.confirm(`Удалить дашборд «${dash.title}»?`)) return
    try {
      await dashboardsApi.remove(id)
      await refreshDashboards()
      go('#/dashboards')
    } catch (e) {
      notify(`Не удалось удалить: ${e.message}`)
    }
  }

  if (error) {
    return (
      <div className="page">
        <p className="error-text">Не удалось открыть дашборд: {error}</p>
        <a href="#/dashboards">← Все дашборды</a>
      </div>
    )
  }
  if (!dash) return <div className="page muted">Загружаю дашборд…</div>

  return (
    <div className="page page-wide">
      <a className="back" href="#/dashboards">
        ← Все дашборды
      </a>
      <div className="page-head">
        {editingTitle ? (
          <InlineEdit
            value={dash.title}
            label="Название дашборда"
            className="title-edit"
            onSave={(t) => {
              setEditingTitle(false)
              setDash({ ...dash, title: t })
              save({ title: t })
            }}
            onCancel={() => setEditingTitle(false)}
          />
        ) : (
          <h2 className="editable" onDoubleClick={() => setEditingTitle(true)}>
            {dash.title}
            <button className="dialog-act" aria-label="Переименовать дашборд" title="Переименовать" onClick={() => setEditingTitle(true)}>
              ✎
            </button>
          </h2>
        )}
        <div className="page-actions">
          <button className="ghost" onClick={exportAll}>
            Скачать всё в Excel
          </button>
          <button className="ghost danger" onClick={removeDashboard}>
            Удалить
          </button>
        </div>
      </div>
      {dash.description && <p className="muted small">{dash.description}</p>}
      {bounds && <PeriodFilter bounds={bounds} period={period} onChange={setPeriod} />}

      {widgets.length === 0 ? (
        <div className="empty">
          <p>На дашборде пока нет виджетов.</p>
          <p className="muted">
            Откройте <a href="#/chat">чат</a>, задайте вопрос и нажмите «+ На дашборд» под ответом.
          </p>
        </div>
      ) : (
        <div className="dash-grid" ref={gridRef}>
          {widgets.map((w, i) => (
            <DashboardWidget
              key={w.id}
              widget={w}
              shown={shown[i]}
              filtered={Boolean(activePeriod) && isPeriodAware(w)}
              unfiltered={Boolean(activePeriod) && !isPeriodAware(w) && Boolean(w.chart || w.table)}
              gridRef={gridRef}
              dragging={dragId === w.id}
              dropTarget={overId === w.id && dragId !== w.id}
              onPatch={(patch, commit) => patchWidget(w.id, patch, commit)}
              onCommit={() => persistWidgets(widgetsRef.current)}
              onMove={(delta) => moveBy(w.id, delta)}
              onRemove={() => {
                if (window.confirm(`Убрать виджет «${w.title}» с дашборда?`)) persistWidgets(widgetsRef.current.filter((x) => x.id !== w.id))
              }}
              dnd={{
                handle: {
                  onDragStart: (e) => {
                    setDragId(w.id)
                    e.dataTransfer.effectAllowed = 'move'
                    e.dataTransfer.setData('text/plain', w.id)
                    const card = e.currentTarget.closest('.widget')
                    if (card) e.dataTransfer.setDragImage(card, 24, 24)
                  },
                  onDragEnd: () => {
                    setDragId(null)
                    setOverId(null)
                  },
                },
                target: {
                  onDragOver: (e) => {
                    if (!dragId || dragId === w.id) return
                    e.preventDefault()
                    if (overId !== w.id) setOverId(w.id)
                  },
                  onDrop: (e) => {
                    e.preventDefault()
                    if (dragId) moveTo(dragId, w.id)
                    setDragId(null)
                    setOverId(null)
                  },
                },
              }}
            />
          ))}
        </div>
      )}
    </div>
  )
}
