// PDF дашборда в браузере: то, что видно на экране (вид графиков, период, размеры виджетов).
// Страница рисуется на своём холсте ограниченного размера: у Safari есть лимит площади холста,
// при превышении он молча отдаёт пустую картинку. Виджеты рисуются напрямую (текст, фон, рамки),
// графики — картинками ECharts, отрисованными заново без анимации.
import * as echarts from 'echarts'
import { applyTheme, getThemeSetting, isDark } from './theme.js'
import { safeFileName } from './export.js'

const PAGE = { w: 210, h: 297, margin: 12 } // A4, мм
const GAP_MM = 4
const MAX_CANVAS_AREA = 12_000_000 // с запасом ниже лимита Safari (≈16,7 млн пикселей)

// Элементы управления в PDF не нужны.
const SKIP = ['drag-handle', 'dialog-act', 'resize-handle', 'result-export', 'view-switch', 'split-head', 'mini']

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const transparent = (c) => !c || c === 'transparent' || /rgba\([^)]*,\s*0\)$/.test(c)

function chartImage(el) {
  const inst = echarts.getInstanceByDom(el)
  if (!inst) return null
  const { width, height } = el.getBoundingClientRect()
  if (width < 2 || height < 2) return null
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

const loadImage = (src) =>
  new Promise((resolve) => {
    const img = new Image()
    img.onload = () => resolve(img)
    img.onerror = () => resolve(null)
    img.src = src
  })

function roundRect(ctx, x, y, w, h, r) {
  const rr = Math.max(0, Math.min(r, w / 2, h / 2))
  ctx.beginPath()
  ctx.moveTo(x + rr, y)
  ctx.arcTo(x + w, y, x + w, y + h, rr)
  ctx.arcTo(x + w, y + h, x, y + h, rr)
  ctx.arcTo(x, y + h, x, y, rr)
  ctx.arcTo(x, y, x + w, y, rr)
  ctx.closePath()
}

// Текст узла с переносом по ширине его блока.
function drawText(ctx, node, style, origin) {
  const text = node.textContent.replace(/\s+/g, ' ').trim()
  if (!text) return
  const range = document.createRange()
  range.selectNodeContents(node)
  const rects = [...range.getClientRects()].filter((r) => r.width > 0 && r.height > 0)
  if (!rects.length) return
  const box = range.getBoundingClientRect()
  const size = parseFloat(style.fontSize) || 14
  ctx.font = `${style.fontStyle} ${style.fontWeight} ${size}px ${style.fontFamily}`
  ctx.fillStyle = style.color
  ctx.textBaseline = 'alphabetic'
  const lineH = rects.length > 1 ? rects[1].top - rects[0].top : rects[0].height
  const maxW = Math.max(box.width, rects[0].width) + 2
  let line = ''
  let y = rects[0].top - origin.y + (rects[0].height + size * 0.72) / 2
  const x = rects[0].left - origin.x
  const lines = []
  for (const word of text.split(' ')) {
    const probe = line ? `${line} ${word}` : word
    if (ctx.measureText(probe).width > maxW && line) {
      lines.push(line)
      line = word
    } else line = probe
  }
  if (line) lines.push(line)
  const left = (i) => (i === 0 ? x : box.left - origin.x)
  lines.forEach((l, i) => ctx.fillText(l, left(i), y + i * lineH))
}

// Рисуем элемент и его детей; графики — готовыми картинками.
function paint(ctx, el, origin, images, clip) {
  if (SKIP.some((c) => el.classList?.contains(c))) return
  const style = getComputedStyle(el)
  if (style.display === 'none' || style.visibility === 'hidden') return
  const r = el.getBoundingClientRect()
  const x = r.left - origin.x
  const y = r.top - origin.y
  if (r.bottom - origin.y < clip.top || r.top - origin.y > clip.bottom) return

  if (el.classList?.contains('chart')) {
    const img = images.get(el)
    if (img) ctx.drawImage(img, x, y, r.width, r.height)
    return
  }

  const radius = parseFloat(style.borderTopLeftRadius) || 0
  if (!transparent(style.backgroundColor)) {
    ctx.fillStyle = style.backgroundColor
    roundRect(ctx, x, y, r.width, r.height, radius)
    ctx.fill()
  }
  const bw = parseFloat(style.borderTopWidth) || 0
  if (bw > 0 && !transparent(style.borderTopColor) && style.borderTopStyle !== 'none') {
    ctx.strokeStyle = style.borderTopColor
    ctx.lineWidth = bw
    roundRect(ctx, x + bw / 2, y + bw / 2, r.width - bw, r.height - bw, radius)
    ctx.stroke()
  } else {
    const bb = parseFloat(style.borderBottomWidth) || 0
    if (bb > 0 && !transparent(style.borderBottomColor) && style.borderBottomStyle !== 'none') {
      ctx.fillStyle = style.borderBottomColor
      ctx.fillRect(x, y + r.height - bb, r.width, bb)
    }
  }

  // Содержимое с прокруткой (таблица в виджете) обрезаем по видимой области.
  const clipped = style.overflow !== 'visible' || style.overflowY !== 'visible'
  if (clipped) {
    ctx.save()
    ctx.beginPath()
    ctx.rect(x, y, r.width, r.height)
    ctx.clip()
  }
  for (const child of el.childNodes) {
    if (child.nodeType === Node.TEXT_NODE) drawText(ctx, child, style, origin)
    else if (child.nodeType === Node.ELEMENT_NODE) paint(ctx, child, origin, images, clip)
  }
  if (clipped) ctx.restore()
}

// Границы рядов: виджет не разрезается между страницами.
function cutPoints(grid) {
  const top = grid.getBoundingClientRect().top
  const boxes = [...grid.querySelectorAll('.widget')].map((w) => {
    const r = w.getBoundingClientRect()
    return { top: r.top - top, bottom: r.bottom - top }
  })
  const tops = [...new Set(boxes.map((b) => Math.round(b.top)))].sort((a, b) => a - b)
  return tops.filter((y) => !boxes.some((b) => b.top < y - 1 && b.bottom > y + 1))
}

function headerCanvas({ title, description, period, widgetCount, width, scale }) {
  const dateText = new Intl.DateTimeFormat('ru-RU', { dateStyle: 'long', timeStyle: 'short' }).format(new Date())
  const meta = [`Сформирован ${dateText}`, `виджетов: ${widgetCount}`]
  if (period) meta.push(`период: ${period}`)
  const font = getComputedStyle(document.body).fontFamily
  const lines = [
    { text: title, font: `700 24px ${font}`, color: '#18181b', h: 32 },
    ...(description ? [{ text: description, font: `400 13px ${font}`, color: '#52525b', h: 20 }] : []),
    { text: `AI-аналитик · ${meta.join(' · ')}`, font: `400 13px ${font}`, color: '#52525b', h: 20 },
  ]
  const height = lines.reduce((s, l) => s + l.h, 8)
  const c = document.createElement('canvas')
  c.width = Math.round(width * scale)
  c.height = Math.round(height * scale)
  const ctx = c.getContext('2d')
  ctx.scale(scale, scale)
  ctx.fillStyle = '#ffffff'
  ctx.fillRect(0, 0, width, height)
  let y = 0
  for (const l of lines) {
    ctx.font = l.font
    ctx.fillStyle = l.color
    y += l.h
    let t = l.text
    while (ctx.measureText(t).width > width && t.length > 1) t = `${t.slice(0, -2)}…`
    ctx.fillText(t, 0, y - 8)
  }
  return { canvas: c, height }
}

export async function downloadDashboardPdf({ grid, title, description, period }) {
  const { jsPDF } = await import('jspdf')

  // На время снимка — светлая тема: PDF печатают на белом.
  const themeBefore = getThemeSetting()
  const wasDark = isDark()
  if (wasDark) {
    applyTheme('light', { persist: false })
    // Пауза, а не requestAnimationFrame: в фоновой вкладке кадры не рисуются, и сборка бы зависла.
    await sleep(400) // графики перерисовываются под тему
  }

  try {
    const width = grid.clientWidth
    if (!width) throw new Error('дашборд не виден на экране — откройте вкладку и повторите')
    const height = grid.scrollHeight
    const origin = grid.getBoundingClientRect()

    // Картинки графиков — заранее, по всем графикам дашборда.
    const images = new Map()
    for (const el of grid.querySelectorAll('.chart')) {
      const url = chartImage(el)
      const img = url && (await loadImage(url))
      if (img) images.set(el, img)
    }

    const pdf = new jsPDF({ orientation: 'portrait', unit: 'mm', format: 'a4' })
    const contentW = PAGE.w - PAGE.margin * 2
    const mmPerPx = contentW / width
    const pageBottom = PAGE.h - PAGE.margin
    const cuts = cutPoints(grid)

    const head = headerCanvas({ title, description, period, widgetCount: grid.querySelectorAll('.widget').length, width, scale: 2 })
    const headerH = head.height * mmPerPx
    pdf.addImage(head.canvas.toDataURL('image/jpeg', 0.92), 'JPEG', PAGE.margin, PAGE.margin, contentW, headerH)
    let y = PAGE.margin + headerH + GAP_MM

    let from = 0
    while (from < height - 1) {
      const room = (pageBottom - y) / mmPerPx
      let to = Math.min(height, from + room)
      if (to < height) {
        const fit = cuts.filter((c) => c > from + 1 && c <= to)
        if (fit.length) to = fit[fit.length - 1]
        else if (y > PAGE.margin + 1) {
          pdf.addPage()
          y = PAGE.margin
          continue
        }
      }
      const sliceH = to - from
      const scale = Math.max(1, Math.min(2, Math.sqrt(MAX_CANVAS_AREA / (width * sliceH))))
      const canvas = document.createElement('canvas')
      canvas.width = Math.round(width * scale)
      canvas.height = Math.round(sliceH * scale)
      const ctx = canvas.getContext('2d')
      ctx.scale(scale, scale)
      ctx.fillStyle = '#ffffff'
      ctx.fillRect(0, 0, width, sliceH)
      ctx.translate(0, -from)
      const sliceOrigin = { x: origin.left, y: origin.top }
      for (const w of grid.querySelectorAll('.widget')) paint(ctx, w, sliceOrigin, images, { top: from, bottom: to })
      pdf.addImage(canvas.toDataURL('image/jpeg', 0.92), 'JPEG', PAGE.margin, y, contentW, sliceH * mmPerPx)
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
    if (wasDark) applyTheme(themeBefore, { persist: false })
  }
}
