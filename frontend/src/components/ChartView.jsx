import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react'
import * as echarts from 'echarts'
import { isDark, onThemeChange } from '../lib/theme.js'
import { formatCompact, formatValue, prettyCategories, unitOf } from '../lib/format.js'

const PALETTE = ['#2f6fed', '#f59e0b', '#16a34a', '#db2777', '#7c3aed', '#0891b2']

function buildOption(spec, dark) {
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

  return {
    ...base,
    grid: { left: 48, right: 16, top: 48, bottom: spec.series.length > 1 ? 40 : 24, containLabel: true },
    xAxis: {
      type: 'category',
      data: labels,
      name: spec.x_label,
      axisLine: { lineStyle: { color: grid } },
      axisLabel: { color: text, interval: labels.length <= 8 ? 0 : 'auto', width: 90, overflow: 'break', hideOverlap: true },
    },
    yAxis: {
      type: 'value',
      name: spec.y_label,
      splitLine: { lineStyle: { color: grid } },
      axisLabel: { color: text, formatter: formatCompact },
    },
    series: spec.series.map((s) => ({
      name: s.name,
      type: spec.type === 'line' ? 'line' : 'bar',
      smooth: spec.type === 'line',
      data: s.data,
    })),
  }
}

const ChartView = forwardRef(function ChartView({ spec, fill = false }, ref) {
  const elRef = useRef(null)
  const chartRef = useRef(null)
  const specRef = useRef(spec)
  specRef.current = spec

  useEffect(() => {
    const chart = echarts.init(elRef.current)
    chartRef.current = chart
    const ro = new ResizeObserver(() => chart.resize())
    ro.observe(elRef.current)
    const off = onThemeChange(() => chart.setOption(buildOption(specRef.current, isDark()), true))
    return () => {
      off()
      ro.disconnect()
      chart.dispose()
      chartRef.current = null
    }
  }, [])

  useEffect(() => {
    chartRef.current?.setOption(buildOption(spec, isDark()), true)
  }, [spec])

  useImperativeHandle(ref, () => ({
    toPng: () =>
      chartRef.current?.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: isDark() ? '#1c1c1f' : '#ffffff' }),
  }))

  return <div className={`chart ${fill ? 'chart-fill' : ''}`} ref={elRef} role="img" aria-label={spec.title || 'График'} />
})

export default ChartView
