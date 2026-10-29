import { useContext, useEffect, useRef, useState } from 'react'
import Message from './Message.jsx'
import { AppContext } from '../lib/appContext.js'
import { askedTextFor, questionFor } from '../lib/dashboardsApi.js'

const SUGGESTIONS = [
  'Опиши данные',
  'Продажи по каналам',
  'Динамика продаж по месяцам',
]

export default function Chat({ conversation, loading, progress, onSend, onChangeView, onPatchMessage, onBuildDashboard, onChangeDatasource, onClearTable }) {
  const { datasources } = useContext(AppContext)
  const [input, setInput] = useState('')
  const [building, setBuilding] = useState(false)
  const listRef = useRef(null)
  const count = conversation.messages.length

  // Вниз прокручиваем только при новом сообщении, ожидании ответа или смене диалога.
  // Смена вида графика меняет сообщение, но не их число — позиция чтения сохраняется.
  // Прокручиваем сам список, а не страницу целиком.
  useEffect(() => {
    const el = listRef.current
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
  }, [conversation.id, count, loading])

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

  return (
    <div className="chat">
      {(resultCount > 0 || datasources.length > 1 || conversation.tableName) && (
        <div className="chat-head">
          {datasources.length > 1 ? (
            <label className="source-pick">
              <span className="muted small">Источник</span>
              <select
                value={conversation.datasourceId || datasources.find((d) => d.is_default)?.id || ''}
                onChange={(e) => onChangeDatasource(conversation.id, e.target.value)}
                disabled={loading}
              >
                {datasources.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                    {d.tables_count ? ` · таблиц: ${d.tables_count}` : ''}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <span className="muted small">Результатов в диалоге: {resultCount}</span>
          )}
          {conversation.tableName && (
            <span className="chip chip-static" title="Вопросы в этом диалоге — только по этой таблице">
              Таблица: {conversation.tableName}
              <button className="dialog-act" aria-label="Снять ограничение по таблице" onClick={() => onClearTable(conversation.id)}>
                ×
              </button>
            </span>
          )}
          {resultCount > 0 && (
            <button className="ghost" onClick={build} disabled={building}>
              {building ? 'Собираю…' : 'Собрать дашборд'}
            </button>
          )}
        </div>
      )}
      <div className="messages" ref={listRef}>
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

        {conversation.messages.map((m, i) => {
          const question = m.role === 'assistant' ? questionFor(conversation.messages, i) : null
          return (
            <Message
              key={m.id}
              msg={m}
              question={question}
              askedText={m.role === 'assistant' ? askedTextFor(conversation.messages, i) : ''}
              datasourceId={conversation.datasourceId}
              tableName={conversation.tableName}
              onPickOption={send}
              onChangeView={(msgId, view) => onChangeView(conversation.id, msgId, view)}
              onPatch={onPatchMessage ? (msgId, patch) => onPatchMessage(conversation.id, msgId, patch) : undefined}
            />
          )
        })}

        {loading && (
          <div className="msg msg-bot">
            <div className="bubble progress-bubble" role="status" aria-live="polite">
              <div className="typing" aria-hidden="true">
                <span />
                <span />
                <span />
              </div>
              <span className="progress-step">{progress?.step || 'Отправляю запрос…'}</span>
              {typeof progress?.pct === 'number' && (
                <span className="progress-bar" aria-hidden="true">
                  <span style={{ width: `${Math.max(5, progress.pct)}%` }} />
                </span>
              )}
            </div>
          </div>
        )}
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
