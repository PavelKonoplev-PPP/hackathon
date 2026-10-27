import { useCallback, useContext, useEffect, useRef, useState } from 'react'
import DataTable from './DataTable.jsx'
import { AppContext } from '../lib/appContext.js'
import { MAX_CSV_BYTES, getSchema, isMock, previewCsv, uploadCsv } from '../lib/api.js'
import { formatValue } from '../lib/format.js'

const DELIMITERS = [
  ['', 'Определить автоматически'],
  [';', 'Точка с запятой ( ; )'],
  [',', 'Запятая ( , )'],
  ['\t', 'Табуляция'],
  ['|', 'Вертикальная черта ( | )'],
]
const ENCODINGS = [
  ['utf-8-sig', 'UTF-8 (по умолчанию)'],
  ['utf-8', 'UTF-8 без BOM'],
  ['cp1251', 'Windows-1251'],
]
const DELIM_NAME = { ';': 'точка с запятой', ',': 'запятая', '\t': 'табуляция', '|': 'вертикальная черта' }

function sizeText(bytes) {
  if (bytes >= 1024 * 1024) return `${formatValue(bytes / 1024 / 1024)} МБ`
  return `${formatValue(Math.ceil(bytes / 1024))} КБ`
}

// Данные (BI-08/11/36): загрузка CSV с предпросмотром и список таблиц в базе.
export default function DataPage({ onAskTable }) {
  const { notify, refreshDatasources } = useContext(AppContext)
  const [tables, setTables] = useState(null)
  const [tablesError, setTablesError] = useState(null)
  const [file, setFile] = useState(null)
  const [delimiter, setDelimiter] = useState('')
  const [encoding, setEncoding] = useState('utf-8-sig')
  const [preview, setPreview] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(null)
  const [conflict, setConflict] = useState(null)
  const [result, setResult] = useState(null)
  const [drag, setDrag] = useState(false)
  const inputRef = useRef(null)

  const loadTables = useCallback(async () => {
    try {
      const s = await getSchema()
      setTables(s.tables || [])
      setTablesError(null)
    } catch (e) {
      setTablesError(e.message)
    }
  }, [])

  useEffect(() => {
    loadTables()
  }, [loadTables])

  async function runPreview(f, d, enc) {
    setBusy('preview')
    setError(null)
    setPreview(null)
    setResult(null)
    setConflict(null)
    try {
      setPreview(await previewCsv(f, { delimiter: d, encoding: enc }))
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(null)
    }
  }

  function pick(f) {
    if (!f) return
    setResult(null)
    if (!f.name.toLowerCase().endsWith('.csv')) return setError('Нужен файл с расширением .csv')
    if (f.size > MAX_CSV_BYTES) return setError(`Файл весит ${sizeText(f.size)} — это больше лимита 100 МБ`)
    setFile(f)
    runPreview(f, delimiter, encoding)
  }

  function changeOption(d, enc) {
    setDelimiter(d)
    setEncoding(enc)
    if (file) runPreview(file, d, enc)
  }

  async function upload(replace = false) {
    if (replace && !window.confirm(`Таблица «${preview.table_name}» будет удалена и загружена заново. Продолжить?`)) return
    setBusy('upload')
    setError(null)
    setConflict(null)
    try {
      // Загружаем с теми разделителем и кодировкой, которые показал предпросмотр.
      const r = await uploadCsv(file, { delimiter: preview?.delimiter || delimiter, encoding: preview?.encoding || encoding, replace })
      setResult(r)
      setPreview(null)
      setFile(null)
      notify(`Таблица «${r.table_name}» загружена: ${formatValue(r.row_count)} строк`)
      loadTables()
      refreshDatasources()
    } catch (e) {
      if (e.status === 409) setConflict(e.message)
      else setError(e.message)
    } finally {
      setBusy(null)
    }
  }

  function reset() {
    setFile(null)
    setPreview(null)
    setError(null)
    setConflict(null)
    if (inputRef.current) inputRef.current.value = ''
  }

  return (
    <div className="page">
      <div className="page-head">
        <h2>Данные</h2>
      </div>

      <section className="card">
        <h3 className="card-title">Загрузить CSV</h3>
        {isMock ? (
          <p className="muted">Загрузка файлов работает с запущенным бэкендом (VITE_MOCK=0). В демо-режиме она отключена.</p>
        ) : (
          <>
            <label
              className={`dropzone ${drag ? 'dropzone-on' : ''}`}
              onDragOver={(e) => {
                e.preventDefault()
                setDrag(true)
              }}
              onDragLeave={() => setDrag(false)}
              onDrop={(e) => {
                e.preventDefault()
                setDrag(false)
                pick(e.dataTransfer.files?.[0])
              }}
            >
              <input ref={inputRef} type="file" accept=".csv,text/csv" onChange={(e) => pick(e.target.files?.[0])} disabled={Boolean(busy)} />
              <span>
                {file ? (
                  <>
                    Выбран <strong>{file.name}</strong> · выбрать другой
                  </>
                ) : (
                  <>
                    <strong>Выберите файл</strong> или перетащите его сюда
                  </>
                )}
              </span>
              <span className="muted small">Формат CSV, до 100 МБ. Данные загружаются как есть, обработка — внутри платформы.</span>
            </label>

            <div className="csv-options">
              <label>
                <span className="muted small">Разделитель</span>
                <select value={delimiter} onChange={(e) => changeOption(e.target.value, encoding)} disabled={Boolean(busy)}>
                  {DELIMITERS.map(([v, l]) => (
                    <option key={l} value={v}>
                      {l}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <span className="muted small">Кодировка</span>
                <select value={encoding} onChange={(e) => changeOption(delimiter, e.target.value)} disabled={Boolean(busy)}>
                  {ENCODINGS.map(([v, l]) => (
                    <option key={v} value={v}>
                      {l}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          </>
        )}

        {busy === 'preview' && <p className="muted">Читаю файл…</p>}
        {error && <p className="error-text">Не получилось: {error}</p>}

        {preview && (
          <div className="csv-preview">
            <p>
              <strong>{preview.filename}</strong>
              {file ? <span className="muted"> · {sizeText(file.size)}</span> : null}
            </p>
            <p className="muted small">
              Будет создана таблица <code>{preview.schema_name}.{preview.table_name}</code> · разделитель: {DELIM_NAME[preview.delimiter] || preview.delimiter} · кодировка: {preview.encoding} · колонок: {preview.columns.length}
            </p>
            {preview.warnings?.length > 0 && (
              <ul className="warnings">
                {preview.warnings.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
            )}
            <p className="muted small">Первые строки файла:</p>
            <DataTable table={{ columns: preview.columns, rows: preview.rows }} />
            {conflict ? (
              <div className="conflict" role="alert">
                <p>{conflict}</p>
                <div className="page-actions">
                  <button className="ghost danger" onClick={() => upload(true)} disabled={Boolean(busy)}>
                    Перезаписать таблицу
                  </button>
                  <button className="ghost" onClick={reset} disabled={Boolean(busy)}>
                    Отмена
                  </button>
                </div>
              </div>
            ) : (
              <div className="page-actions">
                <button className="primary" onClick={() => upload(false)} disabled={Boolean(busy)}>
                  {busy === 'upload' ? 'Загружаю…' : 'Загрузить в базу'}
                </button>
                <button className="ghost" onClick={reset} disabled={Boolean(busy)}>
                  Отмена
                </button>
              </div>
            )}
          </div>
        )}

        {result && (
          <div className="success" role="status">
            <p>
              Готово: таблица <code>{result.schema_name}.{result.table_name}</code> — {formatValue(result.row_count)} строк, {result.columns.length} колонок.
              {result.catalog_refreshed ? ' Каталог обновлён, агент уже видит эти данные.' : ''}
            </p>
            {result.warnings?.length > 0 && (
              <ul className="warnings">
                {result.warnings.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
            )}
            <button className="primary" onClick={() => onAskTable(result.table_name)}>
              Спросить об этих данных
            </button>
          </div>
        )}
      </section>

      <section className="card">
        <div className="page-head">
          <h3 className="card-title">Таблицы в базе</h3>
          <button className="ghost" onClick={loadTables}>
            Обновить
          </button>
        </div>
        {tablesError && <p className="error-text">Не удалось загрузить список таблиц: {tablesError}</p>}
        {tables && tables.length === 0 && !tablesError && <p className="muted">Таблиц пока нет — загрузите CSV выше.</p>}
        {tables && tables.length > 0 && (
          <ul className="table-list">
            {tables.map((t) => (
              <li key={t.name} className={result?.table_name === t.name ? 'table-new' : ''}>
                <div>
                  <strong>{t.display_name || t.name}</strong>
                  <span className="muted small">
                    {' '}
                    · {t.row_count != null ? `${formatValue(t.row_count)} строк` : 'строк: —'} · колонок: {t.columns?.length ?? '—'}
                    {t.warnings?.length ? ` · предупреждений: ${t.warnings.length}` : ''}
                  </span>
                </div>
                <button className="link" onClick={() => onAskTable(t.name, t.display_name)}>
                  Спросить
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}
