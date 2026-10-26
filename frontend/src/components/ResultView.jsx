import { useMemo, useRef } from 'react'
import ChartView from './ChartView.jsx'
import DataTable from './DataTable.jsx'
import KpiCards from './KpiCards.jsx'
import { VIEWS, deriveTable, pieProblem } from '../lib/chartViews.js'
import { deriveKpis } from '../lib/format.js'
import { downloadDataUrl, downloadXlsx } from '../lib/export.js'

// Результат анализа: переключатель вида, график / таблица / KPI и экспорт (BI-17, BI-19, BI-20).
export default function ResultView({ chart, table, view, onChangeView, title, fill = false, extraTable = false }) {
  const chartRef = useRef(null)
  const noPie = pieProblem(chart)
  const requested = view || chart?.type
  const current = requested === 'pie' && noPie ? 'bar' : requested
  const spec = useMemo(() => (chart && current !== 'table' ? { ...chart, type: current } : null), [chart, current])
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
      {chart && current === 'table' && <DataTable table={table || deriveTable(chart)} fill={fill} />}
      {kpis && <KpiCards items={kpis} />}
      {!chart && !kpis && table && <DataTable table={table} fill={fill} />}
      {extraTable && chart && table && current !== 'table' && <DataTable table={table} />}
    </div>
  )
}
