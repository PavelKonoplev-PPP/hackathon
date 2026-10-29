import { useMemo, useRef } from 'react'
import ChartView from './ChartView.jsx'
import DataTable from './DataTable.jsx'
import KpiCards from './KpiCards.jsx'
import { VIEWS, deriveTable, pieProblem, splitChart } from '../lib/chartViews.js'
import { deriveKpis } from '../lib/format.js'
import { downloadDataUrl, downloadXlsx } from '../lib/export.js'

// Один из отдельных графиков (#103): своя шкала, заголовок, PNG и действия (например, «+ На дашборд»).
function SplitPart({ spec, fill, extra }) {
  const ref = useRef(null)
  return (
    <div className={`split-part ${fill ? 'split-part-fill' : ''}`}>
      <div className="split-head">
        <button
          className="mini"
          title="Скачать этот график картинкой"
          onClick={() => {
            const url = ref.current?.toPng()
            if (url) downloadDataUrl(url, spec.title)
          }}
        >
          PNG
        </button>
        {extra}
      </div>
      <ChartView ref={ref} spec={spec} fill={fill} />
    </div>
  )
}

// Результат анализа: переключатель вида, график / таблица / KPI и экспорт (BI-17, BI-19, BI-20).
// Несколько метрик можно показать одним графиком или отдельными, каждую со своей шкалой (#103).
export default function ResultView({ chart, table, view, onChangeView, split = false, onToggleSplit, extraFor, title, fill = false, extraTable = false }) {
  const chartRef = useRef(null)
  const multi = Boolean(chart && chart.series.length > 1)
  const isSplit = multi && split
  const parts = useMemo(() => (isSplit ? splitChart(chart) : null), [chart, isSplit])
  const noPie = parts ? parts.map(pieProblem).find(Boolean) || null : pieProblem(chart)
  const requested = view || chart?.type
  const current = requested === 'pie' && noPie ? 'bar' : requested
  const spec = useMemo(() => (chart && !parts && current !== 'table' ? { ...chart, type: current } : null), [chart, parts, current])
  const partSpecs = useMemo(() => (parts && current !== 'table' ? parts.map((p) => ({ ...p, type: current })) : null), [parts, current])
  const kpis = useMemo(() => (!chart ? deriveKpis(table) : null), [chart, table])
  const exportTable = table || (chart ? deriveTable(chart) : null)
  const name = title || chart?.title || 'Результат'

  function exportPng() {
    const url = chartRef.current?.toPng()
    if (url) downloadDataUrl(url, name)
  }

  return (
    <div className={`result ${fill ? 'result-fill' : ''}`}>
      {!(fill && kpis) && (
        <div className="result-bar">
          {chart && onChangeView && (
            <div className="view-switch" role="group" aria-label="Вид отображения">
              {VIEWS.map((v) => (
                <button
                  key={v.id}
                  className={v.id === current ? 'view-on' : ''}
                  aria-pressed={v.id === current}
                  disabled={v.id === 'pie' && Boolean(noPie)}
                  title={v.id === 'pie' && noPie ? `Круговая недоступна: ${noPie}` : undefined}
                  onClick={() => onChangeView(v.id)}
                >
                  {v.label}
                </button>
              ))}
            </div>
          )}
          {multi && onToggleSplit && current !== 'table' && (
            <button className="mini" onClick={onToggleSplit} aria-pressed={isSplit} title="Показать метрики на одном графике или на отдельных, каждую со своей шкалой">
              {isSplit ? 'Один график' : 'Отдельные графики'}
            </button>
          )}
          <div className="result-export">
            {spec && (
              <button className="mini" onClick={exportPng} title="Скачать график картинкой">
                PNG
              </button>
            )}
            {exportTable && (
              <button className="mini" onClick={() => downloadXlsx([{ name, table: exportTable }], name)} title="Скачать таблицу в Excel">
                Excel
              </button>
            )}
          </div>
        </div>
      )}

      {spec && <ChartView ref={chartRef} spec={spec} fill={fill} />}
      {partSpecs && (
        <div className={`split ${fill ? 'split-fill' : ''}`}>
          {partSpecs.map((p) => (
            <SplitPart key={p.title} spec={p} fill={fill} extra={extraFor?.(p)} />
          ))}
        </div>
      )}
      {chart && current === 'table' && <DataTable table={table || deriveTable(chart)} fill={fill} />}
      {kpis && <KpiCards items={kpis} />}
      {!chart && !kpis && table && <DataTable table={table} fill={fill} />}
      {extraTable && chart && table && current !== 'table' && <DataTable table={table} />}
    </div>
  )
}
