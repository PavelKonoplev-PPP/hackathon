// PDF дашборда в браузере: то, что видно на экране (вид графиков, период, размеры виджетов).
// PDF-эндпоинт бэкенда пока не понимает формат виджетов (chart/table), поэтому собираем здесь.
import * as echarts from 'echarts'
import { applyTheme, getThemeSetting, isDark } from './theme.js'
import { safeFileName } from './export.js'

const PAGE = { w: 210, h: 297, margin: 12 } // A4, мм
const GAP_MM = 4

// Элементы управления в PDF не нужны.
const SKIP = ['drag-handle', 'dialog-act', 'resize-handle', 'result-export', 'view-switch']
const ignoreElements = (el) => SKIP.some((c) => el.classList?.contains(c))

const nextFrame = () => new Promise((r) => requestAnimationFrame(() => r()))
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

function headerNode({ title, description, period, widgetCount, width }) {
  const el = document.createElement('div')
  el.className = 'pdf-header'
  el.style.width = `${width}px`
  const dateText = new Intl.DateTimeFormat('ru-RU', { dateStyle: 'long', timeStyle: 'short' }).format(new Date())
  const meta = [`Сформирован ${dateText}`, `виджетов: ${widgetCount}`]
  if (period) meta.push(`период: ${period}`)
  el.innerHTML = '<h1></h1><p class="pdf-desc"></p><p class="pdf-meta"></p>'
  el.querySelector('h1').textContent = title
  el.querySelector('.pdf-desc').textContent = description || ''
  el.querySelector('.pdf-meta').textContent = `AI-аналитик · ${meta.join(' · ')}`
  document.body.appendChild(el)
  return el
}

// Срезы сетки по границам рядов: виджет не разрезается между страницами.
function cutPoints(grid) {
  const top = grid.getBoundingClientRect().top
  const boxes = [...grid.querySelectorAll('.widget')].map((w) => {
    const r = w.getBoundingClientRect()
    return { top: r.top - top, bottom: r.bottom - top }
  })
  const tops = [...new Set(boxes.map((b) => Math.round(b.top)))].sort((a, b) => a - b)
  const valid = tops.filter((y) => !boxes.some((b) => b.top < y - 1 && b.bottom > y + 1))
  return { valid, height: grid.scrollHeight }
}

// Картинка графика для PDF: та же настройка ECharts, отрисованная заново без анимации во временном
// контейнере того же размера. Снимок «живого» графика может прийти посреди анимации — без данных.
function renderChartImage(el) {
  const inst = echarts.getInstanceByDom(el)
  if (!inst) return null
  const { width, height } = el.getBoundingClientRect()
  const tmp = document.createElement('div')
  tmp.style.cssText = `position:fixed;left:-10000px;top:0;width:${width}px;height:${height}px`
  document.body.appendChild(tmp)
  const off = echarts.init(tmp, null, { renderer: 'canvas' })
  try {
    off.setOption({ ...inst.getOption(), animation: false })
    return off.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: '#ffffff' })
  } finally {
    off.dispose()
    tmp.remove()
  }
}

// В копии страницы для html2canvas заменяем каждый график его картинкой.
function chartsAsImages(sourceRoot) {
  const images = [...sourceRoot.querySelectorAll('.chart')].map(renderChartImage)
  return (clonedDoc, clonedRoot) => {
    const root = clonedRoot || clonedDoc
    root.querySelectorAll('.chart').forEach((el, i) => {
      if (!images[i]) return
      const img = clonedDoc.createElement('img')
      img.src = images[i]
      img.style.cssText = 'width:100%;height:100%;object-fit:contain;display:block'
      el.replaceChildren(img)
    })
  }
}

function sliceCanvas(canvas, fromPx, toPx) {
  const out = document.createElement('canvas')
  out.width = canvas.width
  out.height = Math.max(1, toPx - fromPx)
  const ctx = out.getContext('2d')
  ctx.fillStyle = '#ffffff'
  ctx.fillRect(0, 0, out.width, out.height)
  ctx.drawImage(canvas, 0, fromPx, canvas.width, out.height, 0, 0, canvas.width, out.height)
  return out
}

export async function downloadDashboardPdf({ grid, title, description, period }) {
  const [{ jsPDF }, { default: html2canvas }] = await Promise.all([import('jspdf'), import('html2canvas')])

  // На время снимка — светлая тема: PDF печатают на белом.
  const themeBefore = getThemeSetting()
  const wasDark = isDark()
  if (wasDark) {
    applyTheme('light')
    await nextFrame()
    await sleep(350) // графики перерисовываются под тему
  }

  const scale = 2
  let header
  try {
    header = headerNode({ title, description, period, widgetCount: grid.querySelectorAll('.widget').length, width: grid.clientWidth })
    const opts = { scale, backgroundColor: '#ffffff', ignoreElements, logging: false, useCORS: true }
    const headerCanvas = await html2canvas(header, opts)
    const swapCharts = chartsAsImages(grid)
    const gridCanvas = await html2canvas(grid, { ...opts, onclone: (doc, el) => swapCharts(doc, el) })
    const { valid, height } = cutPoints(grid)

    const pdf = new jsPDF({ orientation: 'portrait', unit: 'mm', format: 'a4' })
    const contentW = PAGE.w - PAGE.margin * 2
    const mmPerPx = contentW / grid.clientWidth // CSS-пиксели сетки → мм
    const pageBottom = PAGE.h - PAGE.margin

    const headerH = headerCanvas.height / scale * mmPerPx
    pdf.addImage(headerCanvas.toDataURL('image/jpeg', 0.92), 'JPEG', PAGE.margin, PAGE.margin, contentW, headerH)
    let y = PAGE.margin + headerH + GAP_MM

    // Нарезка сетки на страницы по границам рядов.
    let from = 0
    while (from < height - 1) {
      const room = (pageBottom - y) / mmPerPx // сколько CSS-пикселей влезает
      let to = Math.min(height, from + room)
      if (to < height) {
        const fit = valid.filter((c) => c > from + 1 && c <= to)
        if (fit.length) to = fit[fit.length - 1]
        else if (y > PAGE.margin + 1) {
          // ряд не помещается на остаток страницы — переносим на новую
          pdf.addPage()
          y = PAGE.margin
          continue
        }
      }
      const part = sliceCanvas(gridCanvas, Math.round(from * scale), Math.round(to * scale))
      pdf.addImage(part.toDataURL('image/jpeg', 0.92), 'JPEG', PAGE.margin, y, contentW, (to - from) * mmPerPx)
      from = to
      if (from < height - 1) {
        pdf.addPage()
        y = PAGE.margin
      }
    }

    const pages = pdf.getNumberOfPages()
    for (let i = 1; i <= pages; i++) {
      pdf.setPage(i)
      pdf.setFontSize(8)
      pdf.setTextColor(150)
      pdf.text(`${i} / ${pages}`, PAGE.w - PAGE.margin, PAGE.h - 5, { align: 'right' })
    }
    pdf.save(`${safeFileName(title, 'дашборд')}.pdf`)
  } finally {
    header?.remove()
    if (wasDark) applyTheme(themeBefore)
  }
}
