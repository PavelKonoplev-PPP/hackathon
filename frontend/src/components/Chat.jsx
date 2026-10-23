import { useEffect, useRef, useState } from 'react'
import Message from './Message.jsx'

const SUGGESTIONS = [
  'Опиши данные',
  'Продажи по каналам',
  'Динамика продаж по месяцам',
]

export default function Chat({ conversation, loading, onSend }) {
  const [input, setInput] = useState('')
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

  const empty = conversation.messages.length === 0

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

        {conversation.messages.map((m) => (
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
        />
        <button type="submit" disabled={loading || !input.trim()}>
          Отправить
        </button>
      </form>
    </div>
  )
}
