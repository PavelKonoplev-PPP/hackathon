import test from 'node:test'
import assert from 'node:assert/strict'
import { buildBreakdownQuestion, forecastBreakdownReason } from '../src/lib/breakdown.js'

test('split-by uses the last question, never concatenates earlier clarifications', () => {
  const messages = [
    { role: 'user', text: 'Как посчитала прогноз?' },
    { role: 'assistant', clarification: { question: 'Уточните', options: ['A'] } },
    { role: 'user', text: 'Покажи выручку и прибыль отдельно' },
    { role: 'assistant', clarification: { question: 'Уточните', options: ['A'] } },
    { role: 'user', text: 'Количество продаж по месяцам?' },
    { role: 'assistant', chart: { title: 'Количество продаж' } },
  ]
  assert.equal(buildBreakdownQuestion(messages, 5, 'Город'),
    'Количество продаж по месяцам — в разбивке по «Город»')
})

test('split-by ignores view-only commands and does not cross previous completed answers', () => {
  const messages = [
    { role: 'user', text: 'Старая выручка по странам' },
    { role: 'assistant', chart: { title: 'Старая выручка' } },
    { role: 'user', text: 'Сколько продаж по каналам?' },
    { role: 'user', text: 'Покажи кругом' },
    { role: 'assistant', chart: { title: 'Количество продаж' } },
  ]
  assert.equal(buildBreakdownQuestion(messages, 4, 'Город'),
    'Сколько продаж по каналам — в разбивке по «Город»')
  assert.equal(buildBreakdownQuestion(messages, 1, 'Город'),
    'Старая выручка по странам — в разбивке по «Город»')
})

test('split-by keeps a stable source question for follow-up splits', () => {
  const messages = [
    { role: 'user', text: 'Сколько продаж по месяцам?' },
    { role: 'assistant', chart: { title: 'По месяцам' } },
    { role: 'user',
      text: 'Сколько продаж по месяцам — в разбивке по «Город»',
      originalQuestion: 'Сколько продаж по месяцам' },
    { role: 'assistant', chart: { title: 'По месяцам и городам' } },
  ]
  assert.equal(buildBreakdownQuestion(messages, 3, 'Канал продажи'),
    'Сколько продаж по месяцам — в разбивке по «Канал продажи»')
})

test('split-by never submits meaningless empty questions', () => {
  assert.equal(buildBreakdownQuestion([], 0, 'Город'), null)
  assert.equal(buildBreakdownQuestion([{ role: 'assistant' }], 0, 'Город', 'Продажи'),
    'Продажи — в разбивке по «Город»')
})

test('forecast charts require separate group predictions', () => {
  const chart = { title: 'Прибыль', series: [{ name: 'Факт' }, { name: 'Прогноз' }] }
  assert.match(forecastBreakdownReason({ chart }), /пересчитать отдельно/)
  assert.match(forecastBreakdownReason({
    chart: { title: 'Прибыль', series: [{ name: 'Прибыль' }] },
    table: { columns: ['Месяц', 'Прибыль, руб. (прогноз)'] },
  }), /пересчитать отдельно/)
  assert.equal(forecastBreakdownReason({
    chart: { title: 'Продажи', series: [{ name: 'Количество' }] },
  }), null)
})
