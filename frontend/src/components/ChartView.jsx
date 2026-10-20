import { useEffect, useRef } from 'react'
import * as echarts from 'echarts'

const PALETTE = ['#2f6fed', '#f59e0b', '#16a34a', '#db2777', '#7c3aed', '#0891b2']

function buildOption(spec, dark) {
  const text = dark ? '#d4d4d8' : '#3f3f46'
  const grid = dark ? '#3f3f46' : '#e4e4e7'
  const base = {
    color: PALETTE,
    textStyle: { color: text },
    title: { text: spec.title, left: 'left', textStyle: { color: text, fontSize: 14, fontWeight: 600 } },
    tooltip: { trigger: spec.type === 'pie' ? 'item' : 'axis' },
    legend: spec.series.length > 1 || spec.type === 'pie' ? { bottom: 0, textStyle: { color: text } } : undefined,
  }

  if (spec.type === 'pie') {
    const s = spec.series[0]
    return {
      ...base,
      series: [
        {
          type: 'pie',
          radius: ['35%', '65%'],
          data: spec.categories.map((name, i) => ({ name, value: s.data[i] })),
        },
      ],
    }
  }

  return {
    ...base,
    grid: { left: 48, right: 16, top: 48, bottom: spec.series.length > 1 ? 40 : 24, containLabel: true },
    xAxis: {
      type: 'category',
      data: spec.categories,
      name: spec.x_label,
      axisLine: { lineStyle: { color: grid } },
      axisLabel: { color: text },
    },
    yAxis: {
      type: 'value',
      name: spec.y_label,
      splitLine: { lineStyle: { color: grid } },
      axisLabel: { color: text },
    },
    series: spec.series.map((s) => ({
      name: s.name,
      type: spec.type === 'line' ? 'line' : 'bar',
      smooth: spec.type === 'line',
      data: s.data,
    })),
  }
}

export default function ChartView({ spec }) {
  const ref = useRef(null)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    const chart = echarts.init(el)
    const render = () => chart.setOption(buildOption(spec, media.matches), true)
    render()
    const ro = new ResizeObserver(() => chart.resize())
    ro.observe(el)
    media.addEventListener('change', render)
    return () => {
      media.removeEventListener('change', render)
      ro.disconnect()
      chart.dispose()
    }
  }, [spec])

  return <div className="chart" ref={ref} role="img" aria-label={spec.title} />
}
