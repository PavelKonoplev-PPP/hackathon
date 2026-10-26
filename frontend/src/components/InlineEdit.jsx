import { useEffect, useRef, useState } from 'react'

// Поле переименования (BI-23): Enter — сохранить, Esc — отменить.
export default function InlineEdit({ value, onSave, onCancel, label, className = '' }) {
  const [text, setText] = useState(value)
  const ref = useRef(null)
  const done = useRef(false)

  useEffect(() => {
    ref.current?.focus()
    ref.current?.select()
  }, [])

  function finish(save) {
    if (done.current) return
    done.current = true
    const t = text.trim()
    if (save && t && t !== value) onSave(t)
    else onCancel()
  }

  return (
    <input
      ref={ref}
      className={`inline-edit ${className}`}
      aria-label={label}
      value={text}
      maxLength={80}
      onChange={(e) => setText(e.target.value)}
      onBlur={() => finish(true)}
      onClick={(e) => e.stopPropagation()}
      onKeyDown={(e) => {
        if (e.key === 'Enter') {
          e.preventDefault()
          finish(true)
        } else if (e.key === 'Escape') {
          e.preventDefault()
          finish(false)
        }
      }}
    />
  )
}
