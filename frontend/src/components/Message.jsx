import { useState } from 'react'
import ResultView from './ResultView.jsx'
import AddToDashboard from './AddToDashboard.jsx'
import SaveQueryButton from './SaveQueryButton.jsx'
import StepsView, { formatMs } from './StepsView.jsx'

export default function Message({ msg, question, datasourceId, tableName, onPickOption, onChangeView }) {
  const [showSql, setShowSql] = useState(false)
  const [showSteps, setShowSteps] = useState(false)
  const steps = Array.isArray(msg.steps) ? msg.steps : []
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

        {(msg.sql || hasResult || steps.length > 0) && (
          <div className="msg-actions">
            {msg.sql && (
              <button className="link" onClick={() => setShowSql((v) => !v)}>
                {showSql ? 'Скрыть, как посчитано' : 'Как посчитано'}
              </button>
            )}
            {hasResult && (
              <AddToDashboard result={{ title, text: msg.text, chart: msg.chart, table: msg.table, sql: msg.sql, view: msg.view, datasourceId }} />
            )}
            {hasResult && msg.sql && (
              <SaveQueryButton result={{ title, question, sql: msg.sql, chart: msg.chart, view: msg.view, datasourceId, tableName }} />
            )}
            {steps.length > 0 && (
              <button className="link" onClick={() => setShowSteps((v) => !v)}>
                {showSteps ? 'Скрыть этапы' : `Этапы: ${steps.length}`}
                {typeof msg.executionMs === 'number' ? ` · ${formatMs(msg.executionMs)}` : ''}
              </button>
            )}
          </div>
        )}
        {showSql && msg.sql && <pre className="sql-code">{msg.sql}</pre>}
        {showSteps && steps.length > 0 && <StepsView steps={steps} />}
      </div>
    </div>
  )
}
