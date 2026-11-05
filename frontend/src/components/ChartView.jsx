import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react'
import * as echarts from 'echarts'
import { isDark, onThemeChange } from '../lib/theme.js'
import { formatCompact, formatValue, prettyCategories, unitOf } from '../lib/format.js'

const PALETTE = ['#2f6fed', '#f59e0b', '#16a34a', '#db2777', '#7c3aed', '#0891b2']

const AXIS_RATIO = 20 // показатели отличаются в 20 раз и больше: на одной шкале меньший не виден
const MIN_SLOT = 64 // уже этого подпись под столбцом не читается: поворачиваем
const CHAR_PX = 7 // примерная ширина буквы при размере шрифта 12

// Подписи оси X: каждую категорию подписываем, слова не разрываем. Длинный ряд (даты) прореживаем сами по себе.
export function categoryLabels(labels, width, color) {
  if (labels.length > 12) return { color, interval: 'auto', hideOverlap: true }
  const slot = ((width || 600) - 70) / Math.max(labels.length, 1)
  const longestWord = Math.max(0, ...labels.flatMap((l) => String(l).split(/[\s-]+/)).map((w) => w.length))
  if (slot >= Math.max(MIN_SLOT, longestWord * CHAR_PX + 6)) {
    return { color, interval: 0, width: slot - 6, overflow: 'break', formatter: (v) => String(v).replace(/-(?=\S)/g, '-\n') }
  }
  return { color, interval: 0, rotate: 35, fontSize: 11, width: 130, overflow: 'truncate' }
}

// Какие серии вынести на правую шкалу: те, что на общей шкале превратились бы в ноль.
export function rightAxisSeries(series) {
  const peak = (s) => Math.max(0, ...(s.data || []).map((v) => Math.abs(Number(v)) || 0))
  const top = Math.max(0, ...series.map(peak))
  return series.map((s) => series.length > 1 && peak(s) > 0 && peak(s) < top / AXIS_RATIO)
}

function buildOption(spec, dark, { width, height } = {}) {
  const text = dark ? '#d4d4d8' : '#3f3f46'
  const grid = dark ? '#3f3f46' : '#e4e4e7'
  const unit = unitOf(spec.y_label || spec.series[0]?.name)
  const fmt = (v) => formatValue(v, unit)
  const labels = prettyCategories(spec.categories || [])
  const base = {
    color: PALETTE,
    backgroundColor: 'transparent',
    textStyle: { color: text },
    title: { text: spec.title, left: 'left', textStyle: { color: text, fontSize: 14, fontWeight: 600 } },
    tooltip: { trigger: spec.type === 'pie' ? 'item' : 'axis', valueFormatter: fmt },
    legend: spec.series.length > 1 || spec.type === 'pie' ? { bottom: 0, type: 'scroll', textStyle: { color: text } } : undefined,
  }

  if (spec.type === 'pie') {
    const s = spec.series[0] || { data: [] }
    return {
      ...base,
      series: [
        {
          type: 'pie',
          radius: ['35%', '62%'],
          label: { color: text },
          data: labels.map((name, i) => ({ name, value: s.data[i] })),
        },
      ],
    }
  }

  const onRight = rightAxisSeries(spec.series)
  const rightNames = spec.series.filter((_, i) => onRight[i]).map((s) => s.name)
  const splitNumber = height && height < 240 ? 3 : 5 // в невысоком виджете подписи шкалы наезжают друг на друга
  const valueAxis = { type: 'value', splitNumber, axisLabel: { color: text, formatter: formatCompact } }
  return {
    ...base,
    legend: base.legend && {
      ...base.legend,
      formatter: (name) => (rightNames.includes(name) ? `${name} (правая шкала)` : name),
    },
    grid: { left: 48, right: 16, top: spec.title ? 48 : 16, bottom: spec.series.length > 1 ? 40 : 24, containLabel: true },
    xAxis: {
      type: 'category',
      data: labels,
      axisLine: { lineStyle: { color: grid } },
      axisLabel: categoryLabels(labels, width, text),
    },
    yAxis: [
      {
        ...valueAxis,
        name: spec.y_label && !String(spec.title || '').includes(spec.y_label) ? spec.y_label : undefined, // без дубля заголовка
        splitLine: { lineStyle: { color: grid } },
      },
      ...(rightNames.length ? [{ ...valueAxis, position: 'right', splitLine: { show: false } }] : []),
    ],
    series: spec.series.map((s, i) => ({
      name: s.name,
      type: spec.type === 'line' ? 'line' : 'bar',
      smooth: spec.type === 'line',
      yAxisIndex: onRight[i] ? 1 : 0,
      data: s.data,
      tooltip: { valueFormatter: (v) => formatValue(v, unitOf(s.name) || (spec.series.length === 1 ? unit : '')) },
    })),
  }
}

const ChartView = forwardRef(function ChartView({ spec, fill = false }, ref) {
  const elRef = useRef(null)
  const chartRef = useRef(null)
  const renderRef = useRef(null)
  const specRef = useRef(spec)
  specRef.current = spec

  useEffect(() => {
    const chart = echarts.init(elRef.current)
    chartRef.current = chart
    let layout = ''
    const layoutKey = () => `${Math.round(chart.getWidth() / 20)}:${chart.getHeight() < 240}`
    const render = () => {
      layout = layoutKey()
      chart.setOption(buildOption(specRef.current, isDark(), { width: chart.getWidth(), height: chart.getHeight() }), true)
    }
    renderRef.current = render
    const ro = new ResizeObserver(() => {
      chart.resize()
      if (layoutKey() !== layout) render() // подписи зависят от размера
    })
    ro.observe(elRef.current)
    const off = onThemeChange(render)
    return () => {
      off()
      ro.disconnect()
      chart.dispose()
      chartRef.current = null
    }
  }, [])

  useEffect(() => {
    renderRef.current?.()
  }, [spec])

  useImperativeHandle(ref, () => ({
    toPng: () =>
      chartRef.current?.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: isDark() ? '#1c1c1f' : '#ffffff' }),
  }))

  return <div className={`chart ${fill ? 'chart-fill' : ''}`} ref={elRef} role="img" aria-label={spec.title || 'График'} />
})

export default ChartView
