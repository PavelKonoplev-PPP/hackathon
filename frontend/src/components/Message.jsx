import { useMemo, useState } from 'react'
import ChartView from './ChartView.jsx'
import DataTable from './DataTable.jsx'
import { VIEWS, deriveTable } from '../lib/chartViews.js'

export default function Message({ msg, onPickOption, onChangeView }) {
  const [showSql, setShowSql] = useState(false)
  const isUser = msg.role === 'user'
  const view = msg.view || msg.chart?.type
  const chartSpec = useMemo(
    () => (msg.chart && view !== 'table' ? { ...msg.chart, type: view } : null),
    [msg.chart, view]
  )

  return (
    <div className={`msg ${isUser ? 'msg-user' : 'msg-bot'}`}>
      <div className={`bubble ${msg.error ? 'bubble-error' : ''}`}>
        <p className="text">{msg.text}</p>

        {msg.chart && (
          <div className="view-switch" role="group" aria-label="Вид отображения">
            {VIEWS.map((v) => (
              <button
                key={v.id}
                className={v.id === view ? 'view-on' : ''}
                aria-pressed={v.id === view}
                onClick={() => onChangeView(msg.id, v.id)}
              >
                {v.label}
              </button>
            ))}
          </div>
        )}
        {chartSpec && <ChartView spec={chartSpec} />}
        {view === 'table' && msg.chart && <DataTable table={msg.table || deriveTable(msg.chart)} />}
        {msg.table && !(view === 'table' && msg.chart) && <DataTable table={msg.table} />}

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

        {msg.sql && (
          <div className="sql">
            <button className="link" onClick={() => setShowSql((v) => !v)}>
              {showSql ? 'Скрыть, как посчитано' : 'Как посчитано'}
            </button>
            {showSql && <pre>{msg.sql}</pre>}
          </div>
        )}
      </div>
    </div>
  )
}
