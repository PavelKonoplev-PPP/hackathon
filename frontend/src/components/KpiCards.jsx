import { formatCompact, formatValue } from '../lib/format.js'

// KPI-карточки (BI-19): показатель, единица, подпись. Большие числа — сокращённо, точное значение в подсказке.
export default function KpiCards({ items }) {
  return (
    <div className="kpis">
      {items.map((k, i) => {
        const exact = formatValue(k.value, k.unit)
        const short = Math.abs(k.value) >= 1e6 ? `${formatCompact(k.value)}${k.unit ? ` ${k.unit}` : ''}` : exact
        return (
          <div className="kpi" key={i}>
            <div className="kpi-label">{k.label}</div>
            <div className="kpi-value" title={exact}>
              {short}
            </div>
            {k.context && <div className="kpi-context">{k.context}</div>}
          </div>
        )
      })}
    </div>
  )
}
