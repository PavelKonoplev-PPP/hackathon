import { useState } from 'react'
import ResultView from './ResultView.jsx'
import InlineEdit from './InlineEdit.jsx'

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v))

// Виджет дашборда: перетаскивание за ⋮⋮, изменение размера за угол (BI-18), переименование (BI-23).
export default function DashboardWidget({ widget, shown, filtered, gridRef, dragging, dropTarget, onPatch, onCommit, onRemove, onMove, dnd }) {
  const [editing, setEditing] = useState(false)
  const w = widget.grid?.w || 6
  const h = widget.grid?.h || 2
  const empty = filtered && !(shown.chart?.categories?.length || shown.table?.rows?.length)

  function startResize(e) {
    e.preventDefault()
    const grid = gridRef.current
    const style = getComputedStyle(grid)
    const gap = parseFloat(style.columnGap) || 16
    const rowH = parseFloat(style.gridAutoRows) || 160
    const colW = (grid.clientWidth - gap * 11) / 12
    const startX = e.clientX
    const startY = e.clientY
    let last = { w, h }
    const move = (ev) => {
      const nw = clamp(Math.round(w + (ev.clientX - startX) / (colW + gap)), 3, 12)
      const nh = clamp(Math.round(h + (ev.clientY - startY) / (rowH + gap)), 1, 4)
      if (nw !== last.w || nh !== last.h) {
        last = { w: nw, h: nh }
        onPatch({ grid: { ...widget.grid, w: nw, h: nh } })
      }
    }
    const up = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      onCommit()
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

  return (
    <section
      className={`widget ${dragging ? 'widget-dragging' : ''} ${dropTarget ? 'widget-drop' : ''}`}
      style={{ '--w': w, '--h': h }}
      aria-label={widget.title}
      {...dnd.target}
    >
      <header className="widget-head">
        <span className="drag-handle" draggable title="Перетащите, чтобы переместить" aria-hidden="true" {...dnd.handle}>
          ⋮⋮
        </span>
        {editing ? (
          <InlineEdit
            value={widget.title}
            label="Название виджета"
            onSave={(t) => {
              setEditing(false)
              onPatch({ title: t }, true)
            }}
            onCancel={() => setEditing(false)}
          />
        ) : (
          <h3 className="widget-title" onDoubleClick={() => setEditing(true)} title="Дважды щёлкните, чтобы переименовать">
            {widget.title}
          </h3>
        )}
        {filtered && <span className="tag">по периоду</span>}
        <button className="dialog-act" aria-label="Переместить раньше" title="Раньше" onClick={() => onMove(-1)}>
          ‹
        </button>
        <button className="dialog-act" aria-label="Переместить позже" title="Позже" onClick={() => onMove(1)}>
          ›
        </button>
        <button className="dialog-act" aria-label={`Переименовать виджет ${widget.title}`} title="Переименовать" onClick={() => setEditing(true)}>
          ✎
        </button>
        <button className="dialog-act dialog-delete" aria-label={`Удалить виджет ${widget.title}`} title="Удалить" onClick={onRemove}>
          ×
        </button>
      </header>
      <div className="widget-body">
        {empty ? (
          <p className="muted small">Нет данных за выбранный период.</p>
        ) : shown.chart || shown.table ? (
          <ResultView
            chart={shown.chart}
            table={shown.table}
            view={widget.grid?.view}
            onChangeView={(v) => onPatch({ grid: { ...widget.grid, view: v } }, true)}
            title={widget.title}
            fill
          />
        ) : (
          <p className="text">{widget.answer || 'Пустой виджет'}</p>
        )}
      </div>
      <span className="resize-handle" onPointerDown={startResize} title="Потяните, чтобы изменить размер" aria-hidden="true" />
    </section>
  )
}
