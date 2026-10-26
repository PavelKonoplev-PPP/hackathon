// Контракт с бэкендом описан в docs/api-contract.md

const USE_MOCK = import.meta.env.VITE_MOCK !== '0'

export const isMock = USE_MOCK

export async function sendMessage(sessionId, message, datasourceId = null) {
  if (USE_MOCK) {
    const { mockReply } = await import('./mock.js')
    return mockReply(message)
  }
  const res = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, message, ...(datasourceId ? { datasource_id: datasourceId } : {}) }),
  })
  if (!res.ok) {
    throw new Error(`Сервер ответил ошибкой ${res.status}`)
  }
  return res.json()
}

const MOCK_SOURCES = [{ id: 'raw', name: 'Данные кейса (9 таблиц)', tables_count: 9, is_default: true }]

// Источники данных (BI-09/12): встроенный raw и подключённые PostgreSQL.
export async function listDatasources() {
  if (USE_MOCK) return MOCK_SOURCES
  const res = await fetch('/api/datasources')
  if (!res.ok) throw new Error(`Сервер ответил ошибкой ${res.status}`)
  return res.json()
}
