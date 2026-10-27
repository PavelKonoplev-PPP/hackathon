import { useContext, useState } from 'react'
import { AppContext } from '../lib/appContext.js'
import { createDatasource, isMock, testDatasource } from '../lib/api.js'
import { countText } from '../lib/format.js'

const EMPTY = { name: '', host: '', port: '5432', dbname: '', user: '', password: '', schema_name: 'public' }
const TABLES = ['таблица', 'таблицы', 'таблиц']

// Источники данных (BI-09/12): список, проверка и подключение внешней PostgreSQL.
export default function DatasourcesSection({ onAskSource }) {
  const { datasources, refreshDatasources, notify } = useContext(AppContext)
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState(EMPTY)
  const [busy, setBusy] = useState(null)
  const [check, setCheck] = useState(null)
  const [error, setError] = useState(null)

  const set = (k) => (e) => {
    setForm((f) => ({ ...f, [k]: e.target.value }))
    setCheck(null)
  }
  const payload = () => ({ ...form, port: Number(form.port) || 5432, name: form.name.trim() || form.dbname })
  const filled = form.host && form.dbname && form.user

  async function runTest() {
    setBusy('test')
    setError(null)
    try {
      setCheck(await testDatasource(payload()))
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(null)
    }
  }

  async function save(e) {
    e.preventDefault()
    setBusy('save')
    setError(null)
    try {
      const ds = await createDatasource(payload())
      notify(`Источник «${ds.name}» подключён: ${countText(ds.tables_count, TABLES)}`)
      setForm(EMPTY) // пароль не держим в памяти страницы
      setCheck(null)
      setOpen(false)
      refreshDatasources()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(null)
    }
  }

  return (
    <section className="card">
      <div className="page-head">
        <h3 className="card-title">Источники данных</h3>
        {!isMock && !open && (
          <button className="ghost" onClick={() => setOpen(true)}>
            Подключить PostgreSQL
          </button>
        )}
      </div>

      <ul className="table-list">
        {datasources.map((d) => (
          <li key={d.id}>
            <div>
              <strong>{d.name}</strong>
              <span className="muted small">
                {' '}
                · {d.is_default ? 'встроенный' : `${d.host}:${d.port}/${d.dbname}`} · {countText(d.tables_count || 0, TABLES)}
              </span>
            </div>
            <button className="link" onClick={() => onAskSource(d)}>
              Спросить
            </button>
          </li>
        ))}
      </ul>

      {open && (
        <form className="ds-form" onSubmit={save} autoComplete="off">
          <p className="muted small">
            Подключение только для чтения. Пароль хранится на сервере в зашифрованном виде и в ответах не возвращается.
          </p>
          <div className="ds-grid">
            <label>
              <span className="muted small">Название</span>
              <input value={form.name} onChange={set('name')} placeholder="Например: CRM" maxLength={255} />
            </label>
            <label>
              <span className="muted small">Хост</span>
              <input value={form.host} onChange={set('host')} placeholder="db.company.local" required />
            </label>
            <label>
              <span className="muted small">Порт</span>
              <input value={form.port} onChange={set('port')} inputMode="numeric" />
            </label>
            <label>
              <span className="muted small">База данных</span>
              <input value={form.dbname} onChange={set('dbname')} required />
            </label>
            <label>
              <span className="muted small">Пользователь</span>
              <input value={form.user} onChange={set('user')} required autoComplete="off" />
            </label>
            <label>
              <span className="muted small">Пароль</span>
              <input type="password" value={form.password} onChange={set('password')} autoComplete="new-password" />
            </label>
            <label>
              <span className="muted small">Схема</span>
              <input value={form.schema_name} onChange={set('schema_name')} />
            </label>
          </div>
          {check && (
            <p className={check.success ? 'ok-text' : 'error-text'} role="status">
              {check.message}
              {check.success && check.tables?.length ? ` — ${check.tables.slice(0, 8).join(', ')}${check.tables.length > 8 ? '…' : ''}` : ''}
            </p>
          )}
          {error && <p className="error-text">Не получилось: {error}</p>}
          <div className="page-actions">
            <button type="button" className="ghost" onClick={runTest} disabled={!filled || Boolean(busy)}>
              {busy === 'test' ? 'Проверяю…' : 'Проверить подключение'}
            </button>
            <button type="submit" className="primary" disabled={!filled || Boolean(busy)}>
              {busy === 'save' ? 'Подключаю…' : 'Подключить'}
            </button>
            <button
              type="button"
              className="ghost"
              onClick={() => {
                setOpen(false)
                setForm(EMPTY)
                setCheck(null)
                setError(null)
              }}
            >
              Отмена
            </button>
          </div>
        </form>
      )}
    </section>
  )
}
