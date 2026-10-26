import { shiftMonths } from '../lib/period.js'

// Общий период для всех графиков дашборда с датами (BI-34).
export default function PeriodFilter({ bounds, period, onChange }) {
  const from = period?.from || ''
  const to = period?.to || ''
  const preset = (months) => onChange({ from: shiftMonths(bounds.max, months), to: bounds.max })

  return (
    <div className="period" role="group" aria-label="Период">
      <span className="period-label">Период</span>
      <input type="date" aria-label="С" min={bounds.min} max={bounds.max} value={from} onChange={(e) => onChange({ from: e.target.value, to })} />
      <span aria-hidden="true">—</span>
      <input type="date" aria-label="По" min={bounds.min} max={bounds.max} value={to} onChange={(e) => onChange({ from, to: e.target.value })} />
      <button className="mini" onClick={() => preset(3)}>3 мес.</button>
      <button className="mini" onClick={() => preset(12)}>12 мес.</button>
      <button className="mini" onClick={() => onChange(null)} disabled={!from && !to}>
        Весь период
      </button>
    </div>
  )
}
