import { useEffect, useRef, useState } from 'react'
import Message from './Message.jsx'

const SUGGESTIONS = [
  'Опиши данные',
  'Продажи по каналам',
  'Динамика продаж по месяцам',
]

export default function Chat({ conversation, loading, onSend, onChangeView, onBuildDashboard }) {
  const [input, setInput] = useState('')
  const [building, setBuilding] = useState(false)
  const endRef = useRef(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [conversation.id, conversation.messages, loading])

  function send(text) {
    if (!text.trim() || loading) return
    onSend(conversation.id, text)
    setInput('')
  }

  function onKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send(input)
    }
  }

  async function build() {
    setBuilding(true)
    try {
      await onBuildDashboard(conversation.id)
    } finally {
      setBuilding(false)
    }
  }

  const empty = conversation.messages.length === 0
  const resultCount = conversation.messages.filter((m) => m.role === 'assistant' && !m.error && (m.chart || m.table)).length
  let question = null

  return (
    <div className="chat">
      {resultCount > 0 && (
        <div className="chat-head">
          <span className="muted small">Результатов в диалоге: {resultCount}</span>
          <button className="ghost" onClick={build} disabled={building}>
            {building ? 'Собираю…' : 'Собрать дашборд'}
          </button>
        </div>
      )}
      <div className="messages">
        {empty && (
          <div className="welcome">
            <h2>Спросите о ваших данных</h2>
            <p>Задайте вопрос обычными словами — я найду ответ и покажу его текстом, графиком или таблицей.</p>
            <div className="chips">
              {SUGGESTIONS.map((s) => (
                <button key={s} className="chip" onClick={() => send(s)}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {conversation.messages.map((m) => {
          if (m.role === 'user') question = m.text
          return (
            <Message
              key={m.id}
              msg={m}
              question={m.role === 'assistant' ? question : null}
              onPickOption={send}
              onChangeView={(msgId, view) => onChangeView(conversation.id, msgId, view)}
            />
          )
        })}

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
          aria-label="Вопрос к данным"
          rows={1}
        />
        <button type="submit" disabled={loading || !input.trim()}>
          Отправить
        </button>
      </form>
    </div>
  )
}
