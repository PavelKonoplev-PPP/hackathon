import { useState } from 'react'
import { applyTheme, getThemeSetting } from '../lib/theme.js'

// Переключатель темы (BI-22).
export default function ThemeSelect() {
  const [theme, setTheme] = useState(getThemeSetting)
  return (
    <select
      className="theme-select"
      aria-label="Тема оформления"
      value={theme}
      onChange={(e) => {
        setTheme(e.target.value)
        applyTheme(e.target.value)
      }}
    >
      <option value="auto">Тема: как в системе</option>
      <option value="light">Светлая тема</option>
      <option value="dark">Тёмная тема</option>
    </select>
  )
}
