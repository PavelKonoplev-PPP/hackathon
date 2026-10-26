import { useContext, useEffect, useRef, useState } from 'react'
import { AppContext } from '../lib/appContext.js'
import { dashboardsApi, widgetFromResult } from '../lib/dashboardsApi.js'
import { dashboardHref } from '../lib/router.js'

// Кнопка «+ На дашборд» под ответом (BI-14): в существующий дашборд или в новый.
export default function AddToDashboard({ result }) {
  const { dashboards, refreshDashboards, notify } = useContext(AppContext)
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [newTitle, setNewTitle] = useState('')
  const boxRef = useRef(null)

  useEffect(() => {
    if (!open) return
    const onDown = (e) => {
      if (!boxRef.current?.contains(e.target)) setOpen(false)
    }
    const onKey = (e) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  async function addTo(dash) {
    setBusy(true)
    try {
      const full = await dashboardsApi.get(dash.id)
      await dashboardsApi.update(dash.id, { widgets: [...full.widgets, widgetFromResult(result)] })
      notify(`Добавлено на дашборд «${full.title}»`, dashboardHref(dash.id))
      setOpen(false)
      refreshDashboards()
    } catch (e) {
      notify(`Не удалось добавить: ${e.message}`)
    } finally {
      setBusy(false)
    }
  }

  async function createNew(e) {
    e.preventDefault()
    setBusy(true)
    try {
      const d = await dashboardsApi.create({ title: newTitle.trim() || result.title || 'Новый дашборд', widgets: [widgetFromResult(result)] })
      notify(`Создан дашборд «${d.title}»`, dashboardHref(d.id))
      setOpen(false)
      setNewTitle('')
      refreshDashboards()
    } catch (err) {
      notify(`Не удалось создать дашборд: ${err.message}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="add-dash" ref={boxRef}>
      <button className="link" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        + На дашборд
      </button>
      {open && (
        <div className="popover" role="dialog" aria-label="Добавить на дашборд">
          {dashboards.length > 0 && (
            <>
              <p className="popover-title">Добавить на дашборд</p>
              <ul>
                {dashboards.map((d) => (
                  <li key={d.id}>
                    <button disabled={busy} onClick={() => addTo(d)}>
                      {d.title}
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
          <form onSubmit={createNew} className="popover-new">
            <input
              placeholder="Название нового дашборда"
              aria-label="Название нового дашборда"
              value={newTitle}
              onChange={(e) => setNewTitle(e.target.value)}
              disabled={busy}
              maxLength={80}
            />
            <button type="submit" disabled={busy}>
              Создать
            </button>
          </form>
        </div>
      )}
    </div>
  )
}
