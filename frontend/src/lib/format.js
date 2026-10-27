// Форматирование чисел и дат для бизнес-пользователя.

const nf = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 })
const nf1 = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 })
const monthFmt = new Intl.DateTimeFormat('ru-RU', { month: 'short', year: 'numeric', timeZone: 'UTC' })

export function toNumber(v) {
  if (typeof v === 'number') return Number.isFinite(v) ? v : null
  if (typeof v === 'string') {
    const s = v.replace(/[\s ]/g, '').replace(',', '.')
    if (/^-?\d+(\.\d+)?$/.test(s)) return Number(s)
  }
  return null
}

// Единица измерения по названию колонки: «Выручка, руб.» → ₽.
export function unitOf(label = '') {
  const l = String(label).toLowerCase()
  if (/млн|млрд|тыс/.test(l)) return '' // масштаб уже в подписи — не дописываем единицу к числу
  if (/руб|₽|выручк|расход|стоимост|оборот/.test(l)) return '₽'
  if (/%|процент/.test(l)) return '%'
  if (/шт/.test(l)) return 'шт.'
  if (/,\s*ч\.?$|час/.test(l)) return 'ч'
  return ''
}

export function formatValue(v, unit = '') {
  const n = toNumber(v)
  if (n === null) return v === null || v === undefined || v === '' ? '—' : String(v)
  const s = nf.format(n)
  return unit ? `${s} ${unit}` : s
}

export function formatCompact(v) {
  const n = toNumber(v)
  if (n === null) return String(v ?? '')
  const a = Math.abs(n)
  if (a >= 1e9) return `${nf1.format(n / 1e9)} млрд`
  if (a >= 1e6) return `${nf1.format(n / 1e6)} млн`
  if (a >= 1e4) return `${nf1.format(n / 1e3)} тыс.`
  return nf.format(n)
}

// Ячейка таблицы: числа — с разделителями разрядов, коды-строки не трогаем.
export function formatCell(v, column) {
  if (typeof v === 'number') return formatValue(v, unitOf(column))
  if (v === null || v === undefined) return '—'
  return String(v)
}

const NOT_KPI = /код|id|номер|год|месяц|дата|неделя|день/i

// Однострочный результат с числами показываем KPI-карточками.
export function deriveKpis(table) {
  if (!table || !Array.isArray(table.rows) || table.rows.length !== 1 || table.columns.length > 6) return null
  const row = table.rows[0]
  const items = table.columns.map((c, i) => ({ label: c, value: row[i], num: toNumber(row[i]) }))
  const nums = items.filter((it) => it.num !== null && !NOT_KPI.test(it.label))
  if (!nums.length) return null
  const context = items
    .filter((it) => !nums.includes(it))
    .map((it) => `${it.label}: ${it.value ?? '—'}`)
    .join(' · ')
  return nums.map((it) => ({ label: it.label, value: it.num, unit: unitOf(it.label), context }))
}

const DATE_RE = /^(\d{4})-(\d{2})(?:-(\d{2}))?(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?)?/

// Диапазон дат значения: «2024-10» → весь октябрь, «2024-10-05 12:00» → 5 октября.
export function dateRange(v) {
  if (typeof v !== 'string') return null
  const m = DATE_RE.exec(v.trim())
  if (!m) return null
  const y = Number(m[1])
  const mo = Number(m[2])
  if (mo < 1 || mo > 12) return null
  if (m[3]) {
    const d = `${m[1]}-${m[2]}-${m[3]}`
    return { start: d, end: d }
  }
  const last = new Date(Date.UTC(y, mo, 0)).getUTCDate()
  return { start: `${m[1]}-${m[2]}-01`, end: `${m[1]}-${m[2]}-${String(last).padStart(2, '0')}` }
}

export const isDateSeries = (values) => values.length > 0 && values.every((v) => dateRange(v) !== null)

// Подписи категорий: месяцы → «окт. 2024», даты без времени → «05.10.2024».
export function prettyCategories(categories) {
  if (!isDateSeries(categories)) return categories
  const parsed = categories.map((c) => DATE_RE.exec(c.trim()))
  const noTime = parsed.every((m) => !m[4] || (m[4] === '00' && m[5] === '00' && (!m[6] || m[6] === '00')))
  if (!noTime) return categories
  const monthly = parsed.every((m) => !m[3] || m[3] === '01')
  return parsed.map((m) => {
    if (monthly) return monthFmt.format(new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, 1))).replace(/\s?г\.$/, '')
    return `${m[3]}.${m[2]}.${m[1]}`
  })
}

// Склонение: plural(4, ['строка', 'строки', 'строк']) → «строки».
export function plural(n, [one, few, many]) {
  const a = Math.abs(n) % 100
  const b = a % 10
  if (a > 10 && a < 20) return many
  if (b === 1) return one
  if (b >= 2 && b <= 4) return few
  return many
}

export const countText = (n, forms) => `${formatValue(n)} ${plural(n, forms)}`
