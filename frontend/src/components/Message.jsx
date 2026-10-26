import { useState } from 'react'
import ResultView from './ResultView.jsx'
import AddToDashboard from './AddToDashboard.jsx'

export default function Message({ msg, question, onPickOption, onChangeView }) {
  const [showSql, setShowSql] = useState(false)
  const isUser = msg.role === 'user'
  const hasResult = !msg.error && (msg.chart || msg.table)
  const title = msg.chart?.title || question || 'Результат'

  return (
    <div className={`msg ${isUser ? 'msg-user' : 'msg-bot'}`}>
      <div className={`bubble ${msg.error ? 'bubble-error' : ''} ${hasResult ? 'bubble-wide' : ''}`}>
        <p className="text">{msg.text}</p>

        {hasResult && (
          <ResultView
            chart={msg.chart}
            table={msg.table}
            view={msg.view}
            onChangeView={(v) => onChangeView(msg.id, v)}
            title={title}
            extraTable
          />
        )}

        {msg.link && (
          <a className="link-btn" href={msg.link.href}>
            {msg.link.label}
          </a>
        )}

        {msg.clarification && (
          <div className="clarify">
            <p>{msg.clarification.question}</p>
            <div className="chips">
              {msg.clarification.options.map((o) => (
                <button key={o} className="chip" onClick={() => onPickOption(o)}>
                  {o}
                </button>
              ))}
            </div>
          </div>
        )}

        {(msg.sql || hasResult) && (
          <div className="msg-actions">
            {msg.sql && (
              <button className="link" onClick={() => setShowSql((v) => !v)}>
                {showSql ? 'Скрыть, как посчитано' : 'Как посчитано'}
              </button>
            )}
            {hasResult && (
              <AddToDashboard result={{ title, text: msg.text, chart: msg.chart, table: msg.table, sql: msg.sql, view: msg.view }} />
            )}
          </div>
        )}
        {showSql && msg.sql && <pre className="sql-code">{msg.sql}</pre>}
      </div>
    </div>
  )
}
