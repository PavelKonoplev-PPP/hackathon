// Дашборды: сервер /api/dashboards (BI-15) или локальное хранилище в демо-режиме.
import { isMock, request } from './api.js'
import { deriveKpis } from './format.js'
import { detectViewRequest } from './chartViews.js'

const LOCAL_KEY = 'bi-dashboards-local-v1'

const http = (path, options) => request(path, options)

const remote = {
  list: () => http('/dashboards'),
  get: (id) => http(`/dashboards/${encodeURIComponent(id)}`),
  create: (body) => http('/dashboards', { method: 'POST', body: JSON.stringify(body) }),
  update: (id, body) => http(`/dashboards/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify(body) }),
  remove: (id) => http(`/dashboards/${encodeURIComponent(id)}`, { method: 'DELETE' }),
}

function readLocal() {
  try {
    const parsed = JSON.parse(localStorage.getItem(LOCAL_KEY) || '[]')
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

function writeLocal(items) {
  try {
    localStorage.setItem(LOCAL_KEY, JSON.stringify(items))
  } catch {
    throw new Error('Не удалось сохранить дашборд в браузере')
  }
}

const summary = (d) => ({
  id: d.id,
  title: d.title,
  description: d.description ?? null,
  datasource_id: d.datasource_id || 'raw',
  widget_count: d.widgets.length,
  created_at: d.created_at,
  updated_at: d.updated_at,
})

const local = {
  async list() {
    return readLocal().map(summary)
  },
  async get(id) {
    const d = readLocal().find((x) => x.id === id)
    if (!d) throw new Error('Дашборд не найден')
    return d
  },
  async create(body) {
    const now = new Date().toISOString()
    const d = { id: `dash_${crypto.randomUUID().slice(0, 8)}`, datasource_id: 'raw', description: null, widgets: [], ...body, created_at: now, updated_at: now }
    writeLocal([...readLocal(), d])
    return d
  },
  async update(id, body) {
    const items = readLocal()
    const i = items.findIndex((x) => x.id === id)
    if (i < 0) throw new Error('Дашборд не найден')
    const patch = Object.fromEntries(Object.entries(body).filter(([, v]) => v !== undefined && v !== null))
    items[i] = { ...items[i], ...patch, updated_at: new Date().toISOString() }
    writeLocal(items)
    return items[i]
  },
  async remove(id) {
    writeLocal(readLocal().filter((x) => x.id !== id))
    return null
  },
}

export const dashboardsApi = isMock ? local : remote

export const shortId = () => crypto.randomUUID().replace(/-/g, '').slice(0, 8)

// Виджет из ответа агента. Вид графика и размер храним в grid (бэкенд сохраняет его как есть).
export function widgetFromResult({ title, text, chart, table, sql, view }) {
  const kpis = !chart && deriveKpis(table)
  return {
    id: shortId(),
    title: title || chart?.title || 'Результат',
    type: chart ? 'chart' : kpis ? 'metric' : table ? 'table' : 'text',
    sql: sql || null,
    chart: chart || null,
    table: table || null,
    answer: text || null,
    grid: { w: 6, h: kpis ? 1 : 2, view: view || null },
  }
}

// Вопрос, к которому относится ответ: команды «покажи кругом» пропускаем — они про вид, а не про данные.
export function questionFor(messages, index) {
  for (let i = index - 1; i >= 0; i--) {
    const m = messages[i]
    if (m.role === 'user' && !detectViewRequest(m.text)) return m.text
  }
  return null
}

// Результаты диалога: ответы с графиком или таблицей. Заголовок — вопрос пользователя:
// заголовок графика от бэкенда часто совпадает с подписью оси («Выручка, руб.»).
export function resultsFromMessages(messages) {
  const out = []
  messages.forEach((m, i) => {
    if (m.role !== 'assistant' || m.error || !(m.chart || m.table)) return
    out.push({ ...m, title: questionFor(messages, i) || m.chart?.title })
  })
  return out
}

export async function createDashboardFromMessages(title, messages, datasourceId = null) {
  const widgets = resultsFromMessages(messages).map(widgetFromResult)
  return dashboardsApi.create({ title, description: 'Собран автоматически из диалога', datasource_id: datasourceId || 'raw', widgets })
}

const DASH_WORD = /дашборд|панел[ьи]/
const CREATE_WORD = /созда|сдела|собер|собра|построй|сформир/

export function detectDashboardRequest(text) {
  const t = text.toLowerCase()
  return DASH_WORD.test(t) && CREATE_WORD.test(t)
}
