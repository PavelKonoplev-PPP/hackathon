const secFmt = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 })
export const formatMs = (ms) => (ms >= 1000 ? `${secFmt.format(ms / 1000)} с` : `${Math.round(ms)} мс`)

const MARK = { completed: '✓', failed: '✕', skipped: '–', in_progress: '…', pending: '·' }

// Этапы выполнения запроса (BI-07) с временем каждого.
export default function StepsView({ steps }) {
  return (
    <ol className="steps">
      {steps.map((s, i) => (
        <li key={`${s.step}-${i}`} className={`step step-${s.status || 'completed'}`}>
          <span className="step-mark" aria-hidden="true">
            {MARK[s.status] || '✓'}
          </span>
          <span className="step-title">{s.title}</span>
          {s.details && <span className="muted small"> — {s.details}</span>}
          {typeof s.duration_ms === 'number' && <span className="step-time">{formatMs(s.duration_ms)}</span>}
        </li>
      ))}
    </ol>
  )
}
