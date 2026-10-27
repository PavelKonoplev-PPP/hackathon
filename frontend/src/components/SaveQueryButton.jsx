import { useContext, useState } from 'react'
import { AppContext } from '../lib/appContext.js'
import { queriesApi, savedQueryFromMessage } from '../lib/queriesApi.js'

// «Сохранить запрос» (BI-35): вопрос, SQL и вид графика, чтобы потом перезапустить на свежих данных.
export default function SaveQueryButton({ result }) {
  const { refreshQueries, notify } = useContext(AppContext)
  const [saved, setSaved] = useState(false)
  const [busy, setBusy] = useState(false)

  async function save() {
    setBusy(true)
    try {
      await queriesApi.create(savedQueryFromMessage(result))
      setSaved(true)
      notify(`Запрос «${result.title}» сохранён — он в боковой панели в разделе «Запросы»`)
      refreshQueries()
    } catch (e) {
      notify(`Не удалось сохранить запрос: ${e.message}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <button className="link" onClick={save} disabled={busy || saved}>
      {saved ? 'Запрос сохранён' : 'Сохранить запрос'}
    </button>
  )
}
