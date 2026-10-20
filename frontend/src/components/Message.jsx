import { useState } from 'react'
import ChartView from './ChartView.jsx'
import DataTable from './DataTable.jsx'

export default function Message({ msg, onPickOption }) {
  const [showSql, setShowSql] = useState(false)
  const isUser = msg.role === 'user'

  return (
    <div className={`msg ${isUser ? 'msg-user' : 'msg-bot'}`}>
      <div className={`bubble ${msg.error ? 'bubble-error' : ''}`}>
        <p className="text">{msg.text}</p>

        {msg.chart && <ChartView spec={msg.chart} />}
        {msg.table && <DataTable table={msg.table} />}

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
