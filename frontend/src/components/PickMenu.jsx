import { useEffect, useRef, useState } from 'react'

// Кнопка-ссылка со списком вариантов (как «+ На дашборд»): недоступные варианты — с причиной.
export default function PickMenu({ label, heading, items, onPick }) {
  const [open, setOpen] = useState(false)
  const boxRef = useRef(null)

  useEffect(() => {
    if (!open) return undefined
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

  return (
    <div className="add-dash" ref={boxRef}>
      <button className="link" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        {label}
      </button>
      {open && (
        <div className="popover" role="dialog" aria-label={heading}>
          <p className="popover-title">{heading}</p>
          <ul>
            {items.map((it) => (
              <li key={it.key}>
                <button
                  disabled={Boolean(it.reason)}
                  title={it.reason || undefined}
                  onClick={() => {
                    setOpen(false)
                    onPick(it)
                  }}
                >
                  {it.label}
                  {it.reason && <span className="muted small"> — {it.reason}</span>}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
