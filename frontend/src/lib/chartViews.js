// Виды отображения результата и распознавание просьбы «покажи иначе».
import { dateRange } from './format.js'

export const VIEWS = [
  { id: 'bar', label: 'Гистограмма' },
  { id: 'line', label: 'Линейный' },
  { id: 'pie', label: 'Круговая' },
  { id: 'table', label: 'Таблица' },
]

const PHRASES = {
  bar: 'в виде столбчатой диаграммы',
  line: 'в виде линейного графика',
  pie: 'в виде круговой диаграммы',
  table: 'в виде таблицы',
}

export const viewPhrase = (view) => PHRASES[view]

// Таблица из данных графика: первая колонка — категории, дальше по колонке на серию.
export function deriveTable(chart) {
  return {
    columns: [chart.x_label || 'Категория', ...chart.series.map((s) => s.name)],
    rows: chart.categories.map((cat, i) => [cat, ...chart.series.map((s) => s.data[i])]),
  }
}

// Только формы «покажи чем-то»: так обычный вопрос про «таблицу продаж» не перехватывается.
const PATTERNS = [
  ['pie', /круговой|круговую|кругом|пирог/],
  ['line', /линейн|линией|линиями|линию/],
  ['bar', /столбч|столбц|столбик|гистограмм/],
  ['table', /таблицей|в виде таблицы|как таблицу|таблично/],
]

// Слова, которые не меняют смысл просьбы «покажи то же самое кругом».
// Если после них и слов о виде графика осталось что-то ещё («по месяцам линией»),
// это новый запрос к данным — его отправляем на бэкенд, он сам учтёт и вид графика.
const FILLER = new Set(['покажи', 'показать', 'отобрази', 'отобразить', 'сделай', 'нарисуй', 'построй',
  'давай', 'можно', 'пожалуйста', 'это', 'этот', 'эти', 'тоже', 'самое', 'же', 'то', 'в', 'виде',
  'как', 'а', 'и', 'теперь', 'график', 'графиком', 'графике', 'диаграмма', 'диаграммой', 'диаграмму',
  'диаграмме', 'вид', 'лучше', 'мне', 'его', 'их', 'результат', 'ответ', 'данные', 'на', 'по',
  'форме', 'формате', 'вместо'])

export function detectViewRequest(text) {
  const t = text.toLowerCase()
  if (t.length > 80) return null
  const hit = PATTERNS.find(([, re]) => re.test(t))
  if (!hit) return null
  let rest = t
  for (const [, re] of PATTERNS) rest = rest.replace(new RegExp('(?:' + re.source + ')[а-яё]*', 'g'), ' ')
  const extra = (rest.match(/[а-яёa-z0-9]+/g) || []).filter((w) => !FILLER.has(w))
  return extra.length ? null : hit[0]
}

export const PIE_MAX_PARTS = 12

// Круговая показывает доли одной величины: одна серия, не больше 12 частей, без отрицательных значений.
export function pieProblem(chart) {
  if (!chart) return null
  if (chart.series.length !== 1) return `в графике ${chart.series.length} величины, а круговая показывает доли только одной`
  if (chart.categories.length > PIE_MAX_PARTS) return `частей больше ${PIE_MAX_PARTS} — круг станет нечитаемым`
  if (chart.series[0].data.some((v) => typeof v === 'number' && v < 0)) return 'есть отрицательные значения'
  return null
}

// «график по выручке и график по прибыли», «два графика», «на отдельных графиках» → отдельные графики.
// «на одном графике», «вместе» → один общий.
const SPLIT_RE = /(график|диаграмм)\S*[^.?!]*\sи\s+(график|диаграмм)|(дв[аеу]х?|оба|обе)\s+(отдельн\S*\s+)?(график|диаграмм)|отдельн\S*\s+(график|диаграмм)|по\s+отдельности|раздельн/i
const ONE_RE = /(на\s+одн\S*|общ\S*|совмещ\S*)\s+(график|диаграмм)|вместе/i

export function detectSplitRequest(text = '') {
  const t = String(text).toLowerCase()
  if (ONE_RE.test(t)) return false
  return SPLIT_RE.test(t)
}

// Каждая серия — отдельный график со своей шкалой и заголовком.
export function splitChart(chart) {
  return chart.series.map((s) => ({ ...chart, title: s.name, y_label: s.name, series: [s] }))
}

// Объединение графиков из разных ответов: общая ось X (даты сравниваются как даты), серии рядом.
const catKey = (c) => dateRange(c)?.start ?? String(c)

export function mergeProblem(a, b) {
  if (!a || !b) return 'нет графика'
  const keys = new Set(a.categories.map(catKey))
  if (!b.categories.some((c) => keys.has(catKey(c)))) return 'нет общих значений по оси X'
  return null
}

export function mergeCharts(a, b) {
  const order = []
  const label = new Map()
  for (const c of [...a.categories, ...b.categories]) {
    const k = catKey(c)
    if (!label.has(k)) {
      label.set(k, c)
      order.push(k)
    }
  }
  if (order.every((k) => /^\d{4}-\d{2}-\d{2}$/.test(k))) order.sort()
  const align = (chart, s) => {
    const byKey = new Map(chart.categories.map((c, i) => [catKey(c), s.data[i]]))
    return order.map((k) => (byKey.has(k) ? byKey.get(k) : null))
  }
  const used = new Set()
  const unique = (name) => {
    let n = name
    for (let i = 2; used.has(n); i++) n = `${name} (${i})`
    used.add(n)
    return n
  }
  return {
    type: a.type === 'pie' ? 'bar' : a.type,
    title: '',
    x_label: a.x_label || b.x_label,
    categories: order.map((k) => label.get(k)),
    series: [
      ...a.series.map((s) => ({ name: unique(s.name), data: align(a, s) })),
      ...b.series.map((s) => ({ name: unique(s.name), data: align(b, s) })),
    ],
  }
}

// «Объедини последние графики», «совмести два графика», «наложи графики».
export const detectMergeRequest = (text = '') => /^\s*(объедини|совмести|наложи)/i.test(text)
