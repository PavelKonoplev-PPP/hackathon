// Простая навигация по адресу после «#»: ссылки на дашборды можно открыть повторно.
import { useEffect, useState } from 'react'

export function parseRoute(hash) {
  const parts = hash.replace(/^#\/?/, '').split('/').filter(Boolean)
  if (parts[0] === 'data') return { page: 'data' }
  if (parts[0] === 'dashboards') {
    return parts[1] ? { page: 'dashboard', id: decodeURIComponent(parts[1]) } : { page: 'dashboards' }
  }
  return { page: 'chat' }
}

export const dashboardHref = (id) => `#/dashboards/${encodeURIComponent(id)}`

export function go(href) {
  window.location.hash = href.replace(/^#/, '')
}

export function useRoute() {
  const [hash, setHash] = useState(() => window.location.hash)
  useEffect(() => {
    const onChange = () => setHash(window.location.hash)
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])
  return parseRoute(hash)
}
