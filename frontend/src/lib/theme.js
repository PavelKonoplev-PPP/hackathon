// Тема интерфейса: auto (как в системе), light, dark.
const KEY = 'bi-theme'
const EVENT = 'bi-themechange'

export function getThemeSetting() {
  try {
    return localStorage.getItem(KEY) || 'auto'
  } catch {
    return 'auto'
  }
}

// persist: false — временно (например, на время сборки PDF), без записи в настройки.
export function applyTheme(setting, { persist = true } = {}) {
  const root = document.documentElement
  if (setting === 'auto') delete root.dataset.theme
  else root.dataset.theme = setting
  if (persist) {
    try {
      localStorage.setItem(KEY, setting)
    } catch {
      // без сохранения
    }
  }
  window.dispatchEvent(new Event(EVENT))
}

export function isDark() {
  const t = document.documentElement.dataset.theme
  if (t) return t === 'dark'
  return window.matchMedia('(prefers-color-scheme: dark)').matches
}

export function onThemeChange(cb) {
  const mq = window.matchMedia('(prefers-color-scheme: dark)')
  mq.addEventListener('change', cb)
  window.addEventListener(EVENT, cb)
  return () => {
    mq.removeEventListener('change', cb)
    window.removeEventListener(EVENT, cb)
  }
}
