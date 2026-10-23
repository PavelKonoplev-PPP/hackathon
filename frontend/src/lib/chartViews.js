// Виды отображения результата и распознавание просьбы «покажи иначе».

export const VIEWS = [
  { id: 'bar', label: 'Столбцы' },
  { id: 'line', label: 'Линия' },
  { id: 'pie', label: 'Круг' },
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

export function detectViewRequest(text) {
  const t = text.toLowerCase()
  if (t.length > 80) return null
  const hit = PATTERNS.find(([, re]) => re.test(t))
  return hit ? hit[0] : null
}
