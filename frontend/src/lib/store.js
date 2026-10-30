import { useEffect, useRef, useState } from 'react'
import { abortable, sendMessage } from './api.js'
import { detectMergeRequest, detectViewRequest, mergeCharts, mergeProblem, pieProblem, viewPhrase } from './chartViews.js'
import { askedTextFor, createDashboardFromMessages, detectDashboardRequest, resultsFromMessages } from './dashboardsApi.js'
import { dashboardHref } from './router.js'
import { queriesApi } from './queriesApi.js'

const KEY = 'bi-chat-conversations-v1'
const TITLE_MAX = 40

const uuid = () => crypto.randomUUID()

const NEW_TITLE = 'Новый диалог'

function newConversation(datasourceId = null, tableName = null) {
  const now = Date.now()
  return { id: uuid(), sessionId: uuid(), datasourceId, tableName, title: NEW_TITLE, messages: [], createdAt: now, updatedAt: now }
}

function makeTitle(text) {
  const clean = text.replace(/\s+/g, ' ').trim()
  return clean.length > TITLE_MAX ? clean.slice(0, TITLE_MAX - 1) + '…' : clean
}

function load() {
  try {
    const raw = localStorage.getItem(KEY)
    if (raw) {
      const parsed = JSON.parse(raw)
      if (Array.isArray(parsed.conversations) && parsed.conversations.length) {
        const activeId = parsed.conversations.some((c) => c.id === parsed.activeId)
          ? parsed.activeId
          : parsed.conversations[0].id
        return { conversations: parsed.conversations, activeId }
      }
    }
  } catch {
    // localStorage недоступен или данные повреждены — начинаем с чистого листа
  }
  const first = newConversation()
  return { conversations: [first], activeId: first.id }
}

// Диалоги хранятся только в браузере (localStorage). Бэкенд помнит контекст по sessionId.
export function useConversations({ onDashboardCreated } = {}) {
  const [state, setState] = useState(load)
  const onCreatedRef = useRef(onDashboardCreated)
  onCreatedRef.current = onDashboardCreated
  const [loadingIds, setLoadingIds] = useState([])
  const [progress, setProgress] = useState({}) // этап выполнения по диалогам (BI-07)
  const stateRef = useRef(state)
  const loadingRef = useRef(new Set())
  const controllers = useRef(new Map()) // «Остановить»: AbortController текущего запроса по диалогам
  stateRef.current = state

  useEffect(() => {
    try {
      localStorage.setItem(KEY, JSON.stringify(state))
    } catch {
      // переполнено хранилище или оно закрыто — работаем без сохранения
    }
  }, [state])

  const update = (id, fn) =>
    setState((s) => ({ ...s, conversations: s.conversations.map((c) => (c.id === id ? fn(c) : c)) }))

  const create = () =>
    setState((s) => {
      const active = s.conversations.find((c) => c.id === s.activeId)
      if (active && active.messages.length === 0) return s // пустой диалог уже открыт
      const c = newConversation()
      return { conversations: [c, ...s.conversations], activeId: c.id }
    })

  const patchMessage = (convId, msgId, patch) =>
    update(convId, (c) => ({
      ...c,
      messages: c.messages.map((m) => (m.id === msgId ? { ...m, ...patch } : m)),
    }))

  const setView = (convId, msgId, view) =>
    update(convId, (c) => ({
      ...c,
      messages: c.messages.map((m) => (m.id === msgId ? { ...m, view } : m)),
    }))

  const rename = (id, title) => update(id, (c) => ({ ...c, title }))

  // Дашборд из всех графиков и таблиц диалога (сценарий 5).
  async function buildDashboard(id) {
    const conv = stateRef.current.conversations.find((c) => c.id === id)
    if (!conv || resultsFromMessages(conv.messages).length === 0) return null
    const d = await createDashboardFromMessages(`Дашборд: ${conv.title}`, conv.messages, conv.datasourceId)
    onCreatedRef.current?.()
    return d
  }

  // Источник диалога. У диалога с сообщениями источник не меняем: начинаем новый диалог
  // с новым session_id, чтобы контекст и кэши прошлой базы не смешивались с новой.
  const setDatasource = (id, datasourceId) => {
    const conv = stateRef.current.conversations.find((c) => c.id === id)
    if (!conv || (conv.datasourceId || null) === (datasourceId || null)) return false
    if (conv.messages.length === 0) {
      update(id, (c) => ({ ...c, datasourceId, sessionId: uuid() }))
      return false
    }
    const c = newConversation(datasourceId)
    setState((s) => ({ conversations: [c, ...s.conversations], activeId: c.id }))
    return true
  }

  const select = (id) => setState((s) => ({ ...s, activeId: id }))

  const remove = (id) =>
    setState((s) => {
      let rest = s.conversations.filter((c) => c.id !== id)
      if (rest.length === 0) rest = [newConversation()]
      const activeId = s.activeId === id ? rest[0].id : s.activeId
      return { conversations: rest, activeId }
    })

  // Ответ всегда дописывается в тот диалог, из которого ушёл вопрос,
  // даже если пользователь успел переключиться на другой.
  async function send(id, text) {
    const message = text.trim()
    const conv = stateRef.current.conversations.find((c) => c.id === id)
    if (!message || !conv || loadingRef.current.has(id)) return

    const userMsg = { id: uuid(), role: 'user', text: message }

    // «Покажи то же самое кругом»: меняем вид последнего результата локально, без запроса к бэкенду.
    // «Объедини последние графики»: два последних графика диалога — на одном, локально.
    if (detectMergeRequest(message)) {
      const charts = conv.messages.filter((m) => m.role === 'assistant' && !m.error && m.chart)
      if (charts.length >= 2) {
        mergeResults(id, charts[charts.length - 2].id, charts[charts.length - 1].id, message)
        return
      }
    }

    const wantedView = detectViewRequest(message)
    const source = wantedView && [...conv.messages].reverse().find((m) => m.chart)
    if (source) {
      const problem = wantedView === 'pie' && pieProblem(source.chart)
      const botMsg = {
        id: uuid(),
        role: 'assistant',
        text: problem
          ? `Круговую диаграмму здесь построить нельзя: ${problem}. Показываю гистограммой.`
          : `Вот то же самое ${viewPhrase(wantedView)}.`,
        chart: source.chart,
        table: source.table,
        sql: source.sql,
        tableName: source.tableName || null,
        view: problem ? 'bar' : wantedView,
      }
      update(id, (c) => ({ ...c, messages: [...c.messages, userMsg, botMsg], updatedAt: Date.now() }))
      return
    }

    // «Собери дашборд»: агент дашборды не строит, собираем из результатов диалога на фронтенде.
    if (detectDashboardRequest(message)) {
      update(id, (c) => ({
        ...c,
        title: c.messages.length === 0 ? makeTitle(message) : c.title,
        messages: [...c.messages, userMsg],
        updatedAt: Date.now(),
      }))
      let reply
      if (resultsFromMessages(conv.messages).length === 0) {
        reply = {
          id: uuid(),
          role: 'assistant',
          text: 'Дашборд собирается из графиков и таблиц этого диалога, а их пока нет. Задайте сначала несколько вопросов к данным.',
          clarification: { question: 'Например:', options: ['Продажи по каналам', 'Динамика продаж по месяцам', 'Опиши данные'] },
        }
      } else {
        try {
          const d = await buildDashboard(id)
          reply = {
            id: uuid(),
            role: 'assistant',
            text: `Собрал дашборд «${d.title}». Виджетов: ${d.widgets.length}. Его можно открыть повторно в разделе «Дашборды».`,
            link: { href: dashboardHref(d.id), label: 'Открыть дашборд' },
          }
        } catch (e) {
          reply = { id: uuid(), role: 'assistant', error: true, text: `Не удалось собрать дашборд: ${e.message}` }
        }
      }
      update(id, (c) => ({ ...c, messages: [...c.messages, reply], updatedAt: Date.now() }))
      return
    }

    await askAgent(conv, message, userMsg)
  }

  const startLoading = (id) => {
    loadingRef.current.add(id)
    setLoadingIds((l) => [...l, id])
  }

  const stopLoading = (id) => {
    loadingRef.current.delete(id)
    setLoadingIds((l) => l.filter((x) => x !== id))
    setProgress((p) => {
      const next = { ...p }
      delete next[id]
      return next
    })
  }

  const botFromResult = (reply, extra = {}) => ({
    id: uuid(),
    role: 'assistant',
    text: reply.answer,
    chart: reply.chart,
    table: reply.table,
    sql: reply.sql,
    clarification: reply.clarification,
    status: reply.status,
    tableName: reply.table_name || null,
    steps: reply.steps,
    executionMs: reply.execution_time_ms,
    ...extra,
  })

  const withTitle = (c, text) => (c.messages.length === 0 && c.title === NEW_TITLE ? makeTitle(text) : c.title)

  // Вопрос агенту: этапы выполнения приходят в progress, ответ — в тот же диалог.
  const stoppedMsg = () => ({
    id: uuid(),
    role: 'assistant',
    stopped: true,
    text: 'Ответ остановлен. Можно задать вопрос заново или переформулировать его.',
  })

  // Вопрос агенту: этапы выполнения приходят в progress, ответ — в тот же диалог.
  // sessionId/tableName можно переопределить (например, для «Разбить по…» — отдельный вопрос без контекста).
  async function askAgent(conv, message, userMsg, { sessionId, tableName } = {}) {
    const id = conv.id
    update(id, (c) => ({ ...c, title: withTitle(c, userMsg.text), messages: [...c.messages, userMsg], updatedAt: Date.now() }))
    startLoading(id)
    const ctrl = new AbortController()
    controllers.current.set(id, ctrl)
    let botMsg
    try {
      const reply = await sendMessage(sessionId || conv.sessionId, message, {
        datasourceId: conv.datasourceId,
        tableName: tableName ?? conv.tableName,
        onProgress: (p) => setProgress((s) => ({ ...s, [id]: p })),
        signal: ctrl.signal,
      })
      botMsg = botFromResult(reply)
    } catch (e) {
      botMsg = e.name === 'AbortError' ? stoppedMsg() : { id: uuid(), role: 'assistant', error: true, text: `Не удалось получить ответ: ${e.message}` }
    } finally {
      controllers.current.delete(id)
    }
    update(id, (c) => ({ ...c, messages: [...c.messages, botMsg], updatedAt: Date.now() }))
    stopLoading(id)
  }

  // «Остановить»: перестаём ждать ответ этого диалога.
  const stop = (id) => controllers.current.get(id)?.abort()

  // Два графика из разных ответов — на одном (общая ось X). Разделить обратно — «Отдельные графики».
  function mergeResults(convId, msgIdA, msgIdB, commandText) {
    const conv = stateRef.current.conversations.find((c) => c.id === convId)
    if (!conv) return
    const ia = conv.messages.findIndex((m) => m.id === msgIdA)
    const ib = conv.messages.findIndex((m) => m.id === msgIdB)
    const a = conv.messages[ia]
    const b = conv.messages[ib]
    const titleA = askedTextFor(conv.messages, ia) || a?.chart?.title || 'график 1'
    const titleB = askedTextFor(conv.messages, ib) || b?.chart?.title || 'график 2'
    const userMsg = { id: uuid(), role: 'user', text: commandText || `Объединить «${titleA}» и «${titleB}»` }
    const problem = mergeProblem(a?.chart, b?.chart)
    const reply = problem
      ? { id: uuid(), role: 'assistant', text: `Эти графики нельзя объединить: ${problem}. Объединяются графики с общей осью — например, оба по месяцам или оба по каналам.` }
      : (() => {
          const chart = mergeCharts(a.chart, b.chart)
          return {
            id: uuid(),
            role: 'assistant',
            text: `Объединил на одном графике: ${chart.series.map((s) => `«${s.name}»`).join(', ')}. Разделить обратно — кнопка «Отдельные графики».`,
            chart,
            table: null,
            view: chart.type,
            split: false,
            merged: true,
          }
        })()
    update(convId, (c) => ({ ...c, messages: [...c.messages, userMsg, reply], updatedAt: Date.now() }))
  }

  // «Разбить по признаку»: тот же вопрос с разбивкой — отдельной сессией агента, чтобы механизм
  // уточнений не сохранил прежнюю группировку. Ответ — в этот же диалог.
  async function splitBy(convId, msgId, column) {
    const conv = stateRef.current.conversations.find((c) => c.id === convId)
    if (!conv || loadingRef.current.has(convId)) return
    const idx = conv.messages.findIndex((m) => m.id === msgId)
    const msg = conv.messages[idx]
    if (!msg) return
    const asked = (askedTextFor(conv.messages, idx) || msg.chart?.title || '').replace(/[?.!\s]+$/, '')
    const question = `${asked}. В разбивке по «${column}»`
    await askAgent(conv, question, { id: uuid(), role: 'user', text: `${asked} — в разбивке по «${column}»` }, { sessionId: uuid(), tableName: msg.tableName })
  }

  // Новый диалог по выбранной таблице (например, после загрузки CSV) с первым вопросом.
  async function startConversation({ datasourceId = null, tableName = null, title } = {}, text) {
    const c = newConversation(datasourceId, tableName)
    if (title) c.title = title
    setState((s) => ({ conversations: [c, ...s.conversations], activeId: c.id }))
    if (text) await askAgent(c, text, { id: uuid(), role: 'user', text })
    return c.id
  }

  const clearTable = (id) => update(id, (c) => ({ ...c, tableName: null }))

  // Повторный запуск сохранённого запроса (BI-35): результат приходит в текущий диалог.
  async function runSavedQuery(id, query) {
    const conv = stateRef.current.conversations.find((c) => c.id === id)
    if (!conv || loadingRef.current.has(id)) return
    const userMsg = { id: uuid(), role: 'user', text: `Повторить сохранённый запрос «${query.title}»` }
    update(id, (c) => ({ ...c, title: withTitle(c, query.title), messages: [...c.messages, userMsg], updatedAt: Date.now() }))
    startLoading(id)
    setProgress((s) => ({ ...s, [id]: { step: 'Выполнение сохранённого запроса' } }))
    const ctrl = new AbortController()
    controllers.current.set(id, ctrl)
    let botMsg
    try {
      const r = await abortable(queriesApi.execute(query.id), ctrl.signal)
      botMsg = botFromResult(r.result, {
        view: query.chart_config?.type || null,
        executionMs: r.execution_time_ms ?? r.result?.execution_time_ms,
      })
    } catch (e) {
      botMsg = e.name === 'AbortError' ? stoppedMsg() : { id: uuid(), role: 'assistant', error: true, text: `Не удалось выполнить сохранённый запрос: ${e.message}` }
    } finally {
      controllers.current.delete(id)
    }
    update(id, (c) => ({ ...c, messages: [...c.messages, botMsg], updatedAt: Date.now() }))
    stopLoading(id)
  }

  const active = state.conversations.find((c) => c.id === state.activeId)
  return {
    conversations: state.conversations,
    active,
    loadingIds,
    progress,
    create,
    select,
    remove,
    send,
    setView,
    rename,
    buildDashboard,
    setDatasource,
    patchMessage,
    stop,
    mergeResults,
    splitBy,
    startConversation,
    clearTable,
    runSavedQuery,
  }
}
