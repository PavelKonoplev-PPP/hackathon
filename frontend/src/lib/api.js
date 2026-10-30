// Контракт с бэкендом описан в docs/api-contract.md

const USE_MOCK = import.meta.env.VITE_MOCK !== '0'

export const isMock = USE_MOCK

const POLL_MS = 700
const JOB_TIMEOUT_MS = 180000
export const MAX_CSV_BYTES = 100 * 1024 * 1024

const UNAVAILABLE = 'сервер недоступен, проверьте, что бэкенд запущен'

export function abortError() {
  const e = new Error('Ответ остановлен')
  e.name = 'AbortError'
  return e
}

// Ожидание, которое прерывается кнопкой «Остановить».
const pause = (ms, signal) =>
  new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(abortError())
    const t = setTimeout(resolve, ms)
    signal?.addEventListener('abort', () => {
      clearTimeout(t)
      reject(abortError())
    }, { once: true })
  })

// Любой промис, который можно бросить по кнопке «Остановить» (сам запрос на сервере доработает).
export function abortable(promise, signal) {
  if (!signal) return promise
  return Promise.race([promise, pause(2 ** 31 - 1, signal)])
}

// Запрос к API с понятными ошибками: текст detail от бэкенда или «сервер недоступен».
export async function request(path, options = {}) {
  const isForm = options.body instanceof FormData
  let res
  try {
    res = await fetch(`/api${path}`, {
      ...options,
      headers: isForm ? options.headers : { 'Content-Type': 'application/json', ...(options.headers || {}) },
    })
  } catch (e) {
    if (e?.name === 'AbortError') throw abortError()
    throw new Error(UNAVAILABLE)
  }
  if (!res.ok) {
    let detail = ''
    try {
      detail = (await res.json()).detail
    } catch {
      // тело без JSON
    }
    const err = new Error(
      typeof detail === 'string' && detail ? detail : res.status >= 500 ? UNAVAILABLE : `Сервер ответил ошибкой ${res.status}`
    )
    err.status = res.status
    throw err
  }
  return res.status === 204 ? null : res.json()
}


const chatBody = (sessionId, message, datasourceId, tableName) =>
  JSON.stringify({
    session_id: sessionId,
    message,
    ...(datasourceId ? { datasource_id: datasourceId } : {}),
    ...(tableName ? { table_name: tableName } : {}),
  })

// Вопрос агенту (BI-07): ставим задачу /api/chat/jobs и опрашиваем статус, передавая этапы в onProgress.
// Если бэкенд без задач (404/405) — обычный POST /api/chat.
// signal — кнопка «Остановить»: перестаём ждать ответ (отмены задачи на сервере нет, она доработает сама).
export async function sendMessage(sessionId, message, { datasourceId = null, tableName = null, onProgress, signal } = {}) {
  if (USE_MOCK) {
    const { mockReply } = await import('./mock.js')
    return abortable(mockReply(message, onProgress), signal)
  }
  const body = chatBody(sessionId, message, datasourceId, tableName)
  let job
  try {
    job = await request('/chat/jobs', { method: 'POST', body, signal })
  } catch (e) {
    if (e.status === 404 || e.status === 405) return request('/chat', { method: 'POST', body, signal })
    throw e
  }
  const started = Date.now()
  for (let i = 0; ; i++) {
    await pause(i === 0 ? 250 : POLL_MS, signal) // первый опрос быстрее: короткие ответы приходят сразу
    const st = await request(`/chat/jobs/${encodeURIComponent(job.job_id)}`, { signal })
    onProgress?.({ step: st.current_step, pct: st.progress_pct, steps: st.steps })
    if (st.status === 'completed' && st.result) {
      return { ...st.result, execution_time_ms: st.result.execution_time_ms ?? st.execution_time_ms }
    }
    if (st.status === 'failed') throw new Error(st.error || 'Запрос завершился ошибкой')
    if (Date.now() - started > JOB_TIMEOUT_MS) {
      throw new Error('Запрос выполняется слишком долго (больше 3 минут). Попробуйте сформулировать его проще или уже.')
    }
  }
}

const MOCK_SOURCES = [{ id: 'raw', name: 'Данные кейса (9 таблиц)', tables_count: 9, is_default: true }]

// Источники данных (BI-09/12): встроенный raw и подключённые PostgreSQL.
export async function listDatasources() {
  if (USE_MOCK) return MOCK_SOURCES
  return request('/datasources')
}

// Каталог таблиц (BI-11): название, строки, колонки.
export async function getSchema() {
  if (USE_MOCK) return { tables: [] }
  return request('/schema')
}

function csvForm(file, { delimiter, encoding, extra = {} }) {
  const form = new FormData()
  form.append('file', file)
  if (delimiter) form.append('delimiter', delimiter)
  if (encoding) form.append('encoding', encoding)
  for (const [k, v] of Object.entries(extra)) form.append(k, String(v))
  return form
}

// Загрузка CSV (BI-08/36): предпросмотр без записи и импорт в схему raw с обновлением каталога.
export function previewCsv(file, opts = {}) {
  return request('/csv/preview', { method: 'POST', body: csvForm(file, { ...opts, extra: { preview_rows: 10 } }) })
}

export function uploadCsv(file, { replace = false, ...opts } = {}) {
  return request('/csv/upload', { method: 'POST', body: csvForm(file, { ...opts, extra: { replace } }) })
}

// Внешняя PostgreSQL (BI-09): проверка подключения и регистрация источника.
export function testDatasource(body) {
  return request('/datasources/test', { method: 'POST', body: JSON.stringify(body) })
}

export function createDatasource(body) {
  return request('/datasources', { method: 'POST', body: JSON.stringify(body) })
}
