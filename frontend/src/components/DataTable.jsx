import { formatCell } from '../lib/format.js'

export default function DataTable({ table, fill = false }) {
  if (!table.rows.length) return <p className="muted small">Нет строк для показа.</p>
  return (
    <div className={`table-wrap ${fill ? 'table-fill' : ''}`}>
      <table>
        <thead>
          <tr>
            {table.columns.map((c) => (
              <th key={c}>{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((row, i) => (
            <tr key={i}>
              {row.map((cell, j) => (
                <td key={j} className={typeof cell === 'number' ? 'num' : ''}>
                  {formatCell(cell, table.columns[j])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
