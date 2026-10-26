// Общий фильтр периода дашборда (BI-34): фильтрует уже посчитанные результаты по датам.
import { useState } from 'react'
import { dateRange, isDateSeries } from './format.js'

function dateAxis(widget) {
  const chartDates = widget.chart && isDateSeries(widget.chart.categories || [])
  const rows = widget.table?.rows || []
  const tableDates = rows.length > 0 && isDateSeries(rows.map((r) => r[0]))
  return { chartDates, tableDates }
}

export const isPeriodAware = (widget) => {
  const { chartDates, tableDates } = dateAxis(widget)
  return chartDates || tableDates
}

export function periodBounds(widgets) {
  let min = null
  let max = null
  const take = (v) => {
    const r = dateRange(v)
    if (!r) return
    if (!min || r.start < min) min = r.start
    if (!max || r.end > max) max = r.end
  }
  for (const w of widgets) {
    const { chartDates, tableDates } = dateAxis(w)
    if (chartDates) w.chart.categories.forEach(take)
    if (tableDates) w.table.rows.forEach((r) => take(r[0]))
  }
  return min ? { min, max } : null
}

export function applyPeriod(widget, period) {
  if (!period || (!period.from && !period.to)) return widget
  const inRange = (v) => {
    const r = dateRange(v)
    return r && (!period.from || r.end >= period.from) && (!period.to || r.start <= period.to)
  }
  const { chartDates, tableDates } = dateAxis(widget)
  let { chart, table } = widget
  if (chartDates) {
    const keep = chart.categories.map((c, i) => (inRange(c) ? i : -1)).filter((i) => i >= 0)
    chart = {
      ...chart,
      categories: keep.map((i) => chart.categories[i]),
      series: chart.series.map((s) => ({ ...s, data: keep.map((i) => s.data[i]) })),
    }
  }
  if (tableDates) table = { ...table, rows: table.rows.filter((r) => inRange(r[0])) }
  return { ...widget, chart, table }
}

export function shiftMonths(iso, months) {
  const d = new Date(`${iso}T00:00:00Z`)
  d.setUTCMonth(d.getUTCMonth() - months)
  d.setUTCDate(d.getUTCDate() + 1)
  return d.toISOString().slice(0, 10)
}

const key = (id) => `bi-dash-period-${id}`

export function usePeriod(id) {
  const [period, setPeriodState] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem(key(id)) || 'null')
    } catch {
      return null
    }
  })
  const setPeriod = (p) => {
    setPeriodState(p)
    try {
      if (p) localStorage.setItem(key(id), JSON.stringify(p))
      else localStorage.removeItem(key(id))
    } catch {
      // без сохранения
    }
  }
  return [period, setPeriod]
}
