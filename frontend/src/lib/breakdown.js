import { detectViewRequest } from './chartViews.js'

// Re-ask only the question that led to this result. Joining all previous
// questions can duplicate unrelated requests in the chat bubble.
export function buildBreakdownQuestion(messages, resultIndex, column, fallback = '') {
  if (!column || !Number.isInteger(resultIndex) || resultIndex < 0) return null
  let base = ''
  for (let i = resultIndex - 1; i >= 0; i--) {
    const message = messages[i]
    if (message?.role === 'assistant' && !message.clarification) break
    if (message?.role === 'user' && !detectViewRequest(message.text || '')) {
      base = (message.originalQuestion || message.text || '').trim()
      break
    }
  }
  base = (base || fallback || '').trim().replace(/[?.!\s]+$/, '')
  return base ? `${base} — в разбивке по «${column}»` : null
}

// A ready-made forecast cannot be broken down by adding a single GROUP BY:
// forecasts need to be recalculated independently for each group.
export function forecastBreakdownReason(result) {
  if (!result?.chart) return null
  const labels = [
    result.chart.title || '',
    ...(result.chart.series || []).map((s) => s.name || ''),
    ...(result.table?.columns || []),
  ]
  return labels.some((label) => /прогноз|forecast/i.test(label))
    ? 'Прогноз нужно пересчитать отдельно для каждой группы'
    : null
}
