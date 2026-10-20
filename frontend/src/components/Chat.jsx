import { useEffect, useRef, useState } from 'react'
import Message from './Message.jsx'
import { sendMessage } from '../lib/api.js'

const SUGGESTIONS = [
  'Что есть в этих данных?',
  'Продажи по каналам',
  'Динамика продаж по месяцам',
]

const STORAGE_KEY = 'bi-chat-state'

function loadState() {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY)
    if (raw) return JSON.parse(raw)
  } catch {
    // sessionStorage может быть недоступен — работаем без него
  }
  return { sessionId: crypto.randomUUID(), messages: [] }
}

export default function Chat({ resetKey }) {
  const [state, setState] = useState(loadState)
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const endRef = useRef(null)

  useEffect(() => {
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state))
    } catch {
      // игнорируем
    }
  }, [state])

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [state.messages, loading])

  // Кнопка «Новый диалог» меняет resetKey
  useEffect(() => {
    if (resetKey > 0) {
      setState({ sessionId: crypto.randomUUID(), messages: [] })
      setInput('')
    }
  }, [resetKey])

  async function send(text) {
    const message = text.trim()
    if (!message || loading) return
    const userMsg = { id: crypto.randomUUID(), role: 'user', text: message }
    setState((s) => ({ ...s, messages: [...s.messages, userMsg] }))
    setInput('')
    setLoading(true)
    try {
      const reply = await sendMessage(state.sessionId, message)
      const botMsg = {
        id: crypto.randomUUID(),
        role: 'assistant',
        text: reply.answer,
        chart: reply.chart,
        table: reply.table,
        sql: reply.sql,
        clarification: reply.clarification,
      }
      setState((s) => ({ ...s, messages: [...s.messages, botMsg] }))
    } catch (e) {
      const errMsg = {
        id: crypto.randomUUID(),
        role: 'assistant',
        error: true,
        text: 'Не удалось получить ответ. Проверьте, что сервер запущен, и попробуйте ещё раз.',
      }
      setState((s) => ({ ...s, messages: [...s.messages, errMsg] }))
    } finally {
      setLoading(false)
    }
  }

  function onKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send(input)
    }
  }

  const empty = state.messages.length === 0

  return (
    <div className="chat">
      <div className="messages">
        {empty && (
          <div className="welcome">
            <h2>Спросите о ваших данных</h2>
            <p>Задайте вопрос обычными словами — я найду ответ и покажу его текстом или графиком.</p>
            <div className="chips">
              {SUGGESTIONS.map((s) => (
                <button key={s} className="chip" onClick={() => send(s)}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {state.messages.map((m) => (
          <Message key={m.id} msg={m} onPickOption={send} />
        ))}

        {loading && (
          <div className="msg msg-bot">
            <div className="bubble typing" aria-label="Агент думает">
              <span />
              <span />
              <span />
            </div>
          </div>
        )}
        <div ref={endRef} />
      </div>

      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault()
          send(input)
        }}
      >
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Например: какие продажи по регионам?"
          rows={1}
          disabled={loading}
        />
        <button type="submit" disabled={loading || !input.trim()}>
          Отправить
        </button>
      </form>
    </div>
  )
}
