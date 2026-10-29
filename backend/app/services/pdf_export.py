"""PDF Export Service (BI-40 / Issue #82).

Generates high-quality, professional PDF reports from dashboards and analytical query results
using ReportLab with Cyrillic font support and robust cross-platform fallbacks.
"""

from __future__ import annotations

import io
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

logger = logging.getLogger(__name__)

# Register Cyrillic TTF font once
_FONT_NAME = "Helvetica"
_FONT_BOLD = "Helvetica-Bold"

_CANDIDATE_FONTS: list[tuple[str, str]] = [
    # Windows
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
    ("C:/Windows/Fonts/calibri.ttf", "C:/Windows/Fonts/calibrib.ttf"),
    ("C:/Windows/Fonts/tahoma.ttf", "C:/Windows/Fonts/tahomabd.ttf"),
    # Linux (Ubuntu / Debian / Alpine)
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    # macOS
    ("/Library/Fonts/Arial.ttf", "/Library/Fonts/Arial Bold.ttf"),
    ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
]

for regular_path, bold_path in _CANDIDATE_FONTS:
    if Path(regular_path).exists():
        try:
            pdfmetrics.registerFont(TTFont("ReportFont", regular_path))
            _FONT_NAME = "ReportFont"
            if Path(bold_path).exists():
                pdfmetrics.registerFont(TTFont("ReportFont-Bold", bold_path))
                _FONT_BOLD = "ReportFont-Bold"
            else:
                _FONT_BOLD = "ReportFont"
            logger.info("Registered Cyrillic PDF font: %s", regular_path)
            break
        except Exception as exc:
            logger.warning("Failed to register font %s: %s", regular_path, exc)


def _safe_text(val: Any) -> str:
    """Safely convert any object into a string for PDF paragraph rendering."""
    if val is None:
        return "—"
    s = str(val).strip()
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_pdf_document(
    title: str,
    subtitle: str | None = None,
    datasource_id: str = "raw",
    kpis: list[dict[str, Any]] | None = None,
    table_data: dict[str, Any] | None = None,
    chart_data: dict[str, Any] | None = None,
    sql_text: str | None = None,
    widgets: list[dict[str, Any]] | None = None,
    extra_notes: str | None = None,
) -> bytes:
    """Build a complete PDF report and return raw PDF bytes."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=40,
        rightMargin=40,
        topMargin=40,
        bottomMargin=40,
    )

    base_styles = getSampleStyleSheet()

    # Custom styles
    title_style = ParagraphStyle(
        "ReportTitle",
        parent=base_styles["Heading1"],
        fontName=_FONT_BOLD,
        fontSize=20,
        leading=24,
        textColor=colors.HexColor("#0F172A"),
        spaceAfter=4,
    )

    subtitle_style = ParagraphStyle(
        "ReportSubtitle",
        parent=base_styles["Normal"],
        fontName=_FONT_NAME,
        fontSize=11,
        leading=14,
        textColor=colors.HexColor("#64748B"),
        spaceAfter=12,
    )

    section_header_style = ParagraphStyle(
        "SectionHeader",
        parent=base_styles["Heading2"],
        fontName=_FONT_BOLD,
        fontSize=13,
        leading=16,
        textColor=colors.HexColor("#1E293B"),
        spaceBefore=14,
        spaceAfter=6,
    )

    body_style = ParagraphStyle(
        "ReportBody",
        parent=base_styles["Normal"],
        fontName=_FONT_NAME,
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#334155"),
    )

    code_style = ParagraphStyle(
        "ReportCode",
        parent=base_styles["Code"],
        fontName=_FONT_NAME,
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#0F172A"),
        backColor=colors.HexColor("#F8FAFC"),
        borderColor=colors.HexColor("#E2E8F0"),
        borderWidth=0.5,
        borderPadding=6,
        spaceAfter=10,
    )

    elements: list[Any] = []

    # 1. Header & Metadata
    elements.append(Paragraph(_safe_text(title), title_style))

    now_str = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    meta_line = f"Источник: <b>{_safe_text(datasource_id)}</b> | Сформирован: <b>{now_str}</b> | AI-Native BI"
    if subtitle:
        meta_line = f"{_safe_text(subtitle)}<br/>{meta_line}"
    elements.append(Paragraph(meta_line, subtitle_style))
    elements.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#E2E8F0"), spaceAfter=14))

    # 2. KPI metrics block (if provided)
    if kpis:
        elements.append(Paragraph("Ключевые показатели (KPI)", section_header_style))
        kpi_table_data = [["Показатель", "Значение", "Статус", "Описание"]]
        for k in kpis:
            k_title = Paragraph(_safe_text(k.get("title") or k.get("id")), body_style)
            k_val = Paragraph(f"<b>{_safe_text(k.get('formatted_value') or k.get('value'))}</b>", body_style)
            status_text = "Подтверждён" if k.get("status") == "approved" else "Предложен"
            k_status = Paragraph(status_text, body_style)
            k_desc = Paragraph(_safe_text(k.get("description") or ""), body_style)
            kpi_table_data.append([k_title, k_val, k_status, k_desc])

        kpi_table = Table(kpi_table_data, colWidths=[130, 90, 75, 220])
        kpi_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F5F9")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#0F172A")),
            ("FONTNAME", (0, 0), (-1, -1), _FONT_NAME),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        elements.append(kpi_table)
        elements.append(Spacer(1, 10))

    # 3. Widgets block (if dashboard widgets provided)
    if widgets:
        elements.append(Paragraph("Виджеты дашборда", section_header_style))
        for idx, w in enumerate(widgets, 1):
            w_type = w.get("type", "unknown")
            w_title = w.get("title", f"Виджет #{idx}")
            w_content = w.get("content") or {}

            elements.append(Paragraph(f"<b>{idx}. {_safe_text(w_title)}</b> <i>({_safe_text(w_type)})</i>", body_style))

            if w_type == "metric":
                metric_val = _safe_text(w_content.get("value") or w_content.get("metric") or "—")
                elements.append(Paragraph(f"Значение: <b>{metric_val}</b>", body_style))
            elif w_type == "text":
                txt = _safe_text(w_content.get("text") or w_content.get("content") or "")
                elements.append(Paragraph(txt, body_style))
            elif w_type == "table":
                w_cols = w_content.get("columns") or []
                w_rows = w_content.get("rows") or []
                if w_cols and w_rows:
                    header_row = [Paragraph(f"<b>{_safe_text(c)}</b>", body_style) for c in w_cols[:6]]
                    t_rows = [header_row]
                    for r in w_rows[:15]:
                        t_rows.append([Paragraph(_safe_text(cell), body_style) for cell in r[:6]])
                    col_w = max(40, int(515 / min(len(w_cols), 6)))
                    t_widget = Table(t_rows, colWidths=[col_w] * min(len(w_cols), 6))
                    t_widget.setStyle(TableStyle([
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F8FAFC")),
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
                        ("FONTNAME", (0, 0), (-1, -1), _FONT_NAME),
                        ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ]))
                    elements.append(t_widget)
            elif w_type == "chart":
                chart_t = w_content.get("type", "chart")
                cats = w_content.get("categories") or []
                series = w_content.get("series") or []
                summary_str = f"Тип диаграммы: {_safe_text(chart_t)}. Категорий: {len(cats)}. Серий данных: {len(series)}."
                elements.append(Paragraph(summary_str, body_style))

            elements.append(Spacer(1, 6))

    # 4. Table data (if provided)
    if table_data:
        cols = table_data.get("columns") or []
        rows = table_data.get("rows") or []
        if cols and rows:
            elements.append(Paragraph(f"Табличные данные ({len(rows)} строк)", section_header_style))
            num_cols = min(len(cols), 7)
            col_width = int(515 / num_cols)
            header_row = [Paragraph(f"<b>{_safe_text(c)}</b>", body_style) for c in cols[:num_cols]]
            t_data = [header_row]
            for r in rows[:30]:
                t_data.append([Paragraph(_safe_text(cell), body_style) for cell in r[:num_cols]])

            res_table = Table(t_data, colWidths=[col_width] * num_cols)
            res_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F5F9")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                ("FONTNAME", (0, 0), (-1, -1), _FONT_NAME),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]))
            elements.append(res_table)
            if len(rows) > 30:
                elements.append(Paragraph(f"<i>Отображены первые 30 строк из {len(rows)}.</i>", body_style))
            elements.append(Spacer(1, 10))

    # 5. Chart specification summary (if provided)
    if chart_data:
        c_title = chart_data.get("title") or "Визуализация"
        c_type = chart_data.get("type", "bar")
        cats = chart_data.get("categories") or []
        series = chart_data.get("series") or []
        elements.append(Paragraph(f"Диаграмма: {_safe_text(c_title)} ({_safe_text(c_type)})", section_header_style))

        if cats and series:
            chart_table = [["Категория"] + [s.get("name", f"Серия #{i}") for i, s in enumerate(series, 1)]]
            for cat_idx, cat in enumerate(cats[:15]):
                row = [str(cat)]
                for s in series:
                    s_data = s.get("data") or []
                    val = s_data[cat_idx] if cat_idx < len(s_data) else ""
                    row.append(_safe_text(val))
                chart_table.append(row)

            ct = Table(chart_table)
            ct.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F8FAFC")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
                ("FONTNAME", (0, 0), (-1, -1), _FONT_NAME),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
            ]))
            elements.append(ct)
            elements.append(Spacer(1, 10))

    # 6. SQL Query block (if provided)
    if sql_text:
        elements.append(Paragraph("Исполненный аналитический SQL", section_header_style))
        clean_sql = _safe_text(sql_text)
        elements.append(Paragraph(f"<pre>{clean_sql}</pre>", code_style))

    # 7. Additional notes
    if extra_notes:
        elements.append(Paragraph("Примечания и выводы", section_header_style))
        elements.append(Paragraph(_safe_text(extra_notes), body_style))

    # Build PDF
    doc.build(elements)
    return buf.getvalue()


def export_dashboard_to_pdf(dashboard_id: str) -> bytes:
    """Fetch dashboard by ID from data/dashboards.json and render as PDF."""
    from .dashboards import get_dashboard

    d = get_dashboard(dashboard_id)
    if not d:
        raise ValueError(f"Dashboard '{dashboard_id}' not found")

    widgets_list = [w.model_dump() for w in d.widgets]
    return build_pdf_document(
        title=d.title,
        subtitle=d.description or "Аналитический дашборд",
        datasource_id="raw",
        widgets=widgets_list,
    )
