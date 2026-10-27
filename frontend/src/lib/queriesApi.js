// Сохранённые запросы (BI-35): /api/queries или localStorage в демо-режиме.
import { isMock, request } from './api.js'

const LOCAL_KEY = 'bi-saved-queries-local-v1'

const remote = {
  list: () => request('/queries'),
  create: (body) => request('/queries', { method: 'POST', body: JSON.stringify(body) }),
  update: (id, body) => request(`/queries/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify(body) }),
  remove: (id) => request(`/queries/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  execute: (id) => request(`/queries/${encodeURIComponent(id)}/execute`, { method: 'POST' }),
}

function read() {
  try {
    const v = JSON.parse(localStorage.getItem(LOCAL_KEY) || '[]')
    return Array.isArray(v) ? v : []
  } catch {
    return []
  }
}

function write(items) {
  try {
    localStorage.setItem(LOCAL_KEY, JSON.stringify(items))
  } catch {
    throw new Error('Не удалось сохранить запрос в браузере')
  }
}

const local = {
  async list() {
    return read()
  },
  async create(body) {
    const now = new Date().toISOString()
    const q = { id: `q_${crypto.randomUUID().slice(0, 8)}`, tags: [], datasource_id: 'raw', ...body, created_at: now, updated_at: now }
    write([q, ...read()])
    return q
  },
  async update(id, body) {
    const items = read()
    const i = items.findIndex((q) => q.id === id)
    if (i < 0) throw new Error('Запрос не найден')
    items[i] = { ...items[i], ...body, updated_at: new Date().toISOString() }
    write(items)
    return items[i]
  },
  async remove(id) {
    write(read().filter((q) => q.id !== id))
    return null
  },
  async execute(id) {
    const q = read().find((x) => x.id === id)
    if (!q) throw new Error('Запрос не найден')
    const { mockReply } = await import('./mock.js')
    const result = await mockReply(q.question || q.title)
    return { query_id: id, title: q.title, datasource_id: q.datasource_id, status: 'completed', execution_time_ms: 120, result }
  },
}

export const queriesApi = isMock ? local : remote

// SQL агента написан для временной таблицы `t`, которую строит раннер агента; напрямую в базе
// он не выполнится (relation "t" does not exist). Такой запрос сохраняем без SQL — при запуске
// бэкенд заново задаст вопрос агенту (см. #81).
const AGENT_SQL = /(^|\n)\s*--\s*t\s*=|\bfrom\s+t\b/i

// Запрос из ответа агента: вопрос, SQL и выбранный вид графика.
export function savedQueryFromMessage({ title, question, sql, chart, view, datasourceId, tableName }) {
  return {
    title,
    question: question || title,
    sql: sql && !AGENT_SQL.test(sql) ? sql : null,
    datasource_id: datasourceId || 'raw',
    table_name: tableName || null,
    chart_config: chart ? { type: view || chart.type } : null,
    tags: [],
  }
}
