import { useEffect, useRef, useState } from 'react'
import { sendMessage } from './api.js'
import { detectViewRequest, pieProblem, viewPhrase } from './chartViews.js'
import { createDashboardFromMessages, detectDashboardRequest, resultsFromMessages } from './dashboardsApi.js'
import { dashboardHref } from './router.js'

const KEY = 'bi-chat-conversations-v1'
const TITLE_MAX = 40

const uuid = () => crypto.randomUUID()

function newConversation(datasourceId = null) {
  const now = Date.now()
  return { id: uuid(), sessionId: uuid(), datasourceId, title: 'Новый диалог', messages: [], createdAt: now, updatedAt: now }
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
  const stateRef = useRef(state)
  const loadingRef = useRef(new Set())
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

    update(id, (c) => ({
      ...c,
      title: c.messages.length === 0 ? makeTitle(message) : c.title,
      messages: [...c.messages, userMsg],
      updatedAt: Date.now(),
    }))
    loadingRef.current.add(id)
    setLoadingIds((l) => [...l, id])

    let botMsg
    try {
      const reply = await sendMessage(conv.sessionId, message, conv.datasourceId)
      botMsg = {
        id: uuid(),
        role: 'assistant',
        text: reply.answer,
        chart: reply.chart,
        table: reply.table,
        sql: reply.sql,
        clarification: reply.clarification,
      }
    } catch {
      botMsg = {
        id: uuid(),
        role: 'assistant',
        error: true,
        text: 'Не удалось получить ответ. Проверьте, что сервер запущен, и попробуйте ещё раз.',
      }
    }
    update(id, (c) => ({ ...c, messages: [...c.messages, botMsg], updatedAt: Date.now() }))
    loadingRef.current.delete(id)
    setLoadingIds((l) => l.filter((x) => x !== id))
  }

  const active = state.conversations.find((c) => c.id === state.activeId)
  return {
    conversations: state.conversations,
    active,
    loadingIds,
    create,
    select,
    remove,
    send,
    setView,
    rename,
    buildDashboard,
    setDatasource,
  }
}
