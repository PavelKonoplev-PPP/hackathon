// Колонки таблиц из каталога (/api/schema) — для «Разбить по…». Загружаем один раз.
import { useEffect, useState } from 'react'
import { getSchema } from './api.js'

let cached = null

function loadSchema() {
  if (!cached) {
    cached = getSchema().catch(() => {
      cached = null
      return { tables: [] }
    })
  }
  return cached
}

export function useCategoryColumns(tableName) {
  const [columns, setColumns] = useState([])
  useEffect(() => {
    if (!tableName) return undefined
    let alive = true
    loadSchema().then((s) => {
      const t = (s.tables || []).find((x) => x.name === tableName || x.display_name === tableName)
      const cols = (t?.columns || []).filter((c) => (c.inferred_type || c.type) === 'category').map((c) => c.name)
      if (alive) setColumns(cols)
    })
    return () => {
      alive = false
    }
  }, [tableName])
  return columns
}
