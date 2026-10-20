// Контракт с бэкендом описан в docs/api-contract.md

const USE_MOCK = import.meta.env.VITE_MOCK !== '0'

export const isMock = USE_MOCK

export async function sendMessage(sessionId, message) {
  if (USE_MOCK) {
    const { mockReply } = await import('./mock.js')
    return mockReply(message)
  }
  const res = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, message }),
  })
  if (!res.ok) {
    throw new Error(`Сервер ответил ошибкой ${res.status}`)
  }
  return res.json()
}
