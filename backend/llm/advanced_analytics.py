"""Advanced analytics modules for Hackathon Scenarios 7-10.

Implements:
- Scenario 7: Seasonality patterns and recurring cycles (monthly/quarterly index, peak & trough detection)
- Scenario 8: Customer and operational segmentation (multi-attribute grouping, key differences)
- Scenario 9: Correlations and leading indicators (Pearson coefficient, lag relationships)
- Scenario 10: Extrapolation, trend forecasting, and what-if scenarios (linear trend, sensitivity analysis)
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

# ---------- 1. Сценарий 7: Сезонность и периодические закономерности ----------

SEASONALITY_PATTERN = re.compile(
    r"\b(сезонност\w*|паттерн\w*|повторяющ\w*|периодич\w*|циклич\w*|сезонн\w*)\b|"
    r"\b(зимой|летом|осенью|весной|по\s+месяцам\s+года|по\s+дням\s+недели|пик\w*\s+продаж|спад\w*\s+продаж)\b",
    re.IGNORECASE,
)

MONTH_RU = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"
]


def is_seasonality(question: str) -> bool:
    """Detect if query asks about seasonality or recurring cyclical patterns."""
    return bool(SEASONALITY_PATTERN.search(question))


def seasonality_hint(date_col: str, metric_expr: str = "COUNT(*)") -> str:
    """Generate SQL generation guidance for seasonality analysis."""
    return (
        f"\n[Анализ сезонности]: сгруппируйте данные по месяцам года через "
        f"EXTRACT(MONTH FROM \"{date_col}\")::int AS \"Номер месяца\", "
        f"рассчитайте метрику {metric_expr} и отсортируйте по \"Номер месяца\" ASC.\n"
    )


def analyze_seasonality(columns: list[str], rows: list[list[Any]]) -> dict[str, Any] | None:
    """Analyze monthly or periodic data to calculate seasonality index and identify peaks/troughs."""
    if not rows or len(rows) < 3 or len(columns) < 2:
        return None

    # Find month number/label and metric value
    month_idx = 0
    val_idx = 1
    for i, col in enumerate(columns):
        if "месяц" in col.lower() or "номер" in col.lower():
            month_idx = i
        elif any(k in col.lower() for k in ("выручка", "сумма", "количество", "число", "продажи", "значение")):
            val_idx = i

    parsed_data: list[tuple[str, float]] = []
    for r in rows:
        m_raw = r[month_idx]
        v_raw = r[val_idx]
        if v_raw is None:
            continue
        try:
            val = float(str(v_raw).replace(",", ".").replace(" ", ""))
        except (ValueError, TypeError):
            continue

        # Format month label
        if isinstance(m_raw, (int, float)) and 1 <= int(m_raw) <= 12:
            label = MONTH_RU[int(m_raw) - 1]
        else:
            label = str(m_raw)
        parsed_data.append((label, val))

    if len(parsed_data) < 3:
        return None

    vals = [v for _, v in parsed_data]
    avg_val = sum(vals) / len(vals)
    if avg_val == 0:
        return None

    peaks: list[tuple[str, float, float]] = []  # (label, val, pct_diff)
    troughs: list[tuple[str, float, float]] = []

    for label, val in parsed_data:
        diff_pct = ((val - avg_val) / avg_val) * 100.0
        if diff_pct >= 15.0:
            peaks.append((label, val, diff_pct))
        elif diff_pct <= -15.0:
            troughs.append((label, val, diff_pct))

    max_item = max(parsed_data, key=lambda x: x[1])
    min_item = min(parsed_data, key=lambda x: x[1])

    return {
        "average": avg_val,
        "max": max_item,
        "min": min_item,
        "peaks": peaks,
        "troughs": troughs,
        "data": parsed_data,
    }


def seasonality_summary(analysis: dict[str, Any]) -> str:
    """Generate business explanation for seasonality findings."""
    max_label, max_val = analysis["max"]
    min_label, min_val = analysis["min"]
    avg_val = analysis["average"]

    max_pct = ((max_val - avg_val) / avg_val) * 100.0
    min_pct = ((min_val - avg_val) / avg_val) * 100.0

    lines = [
        f"Анализ сезонности выявил выраженные колебания спроса: среднегодовой уровень составляет {avg_val:,.0f}.".replace(",", " "),
        f"Пиковый период: **{max_label}** ({max_val:,.0f}, на {max_pct:+.1f}% выше среднего).".replace(",", " "),
        f"Период наименьшей активности: **{min_label}** ({min_val:,.0f}, на {min_pct:+.1f}% ниже среднего).".replace(",", " "),
    ]
    if analysis.get("peaks") and len(analysis["peaks"]) > 1:
        other_peaks = [f"{l} ({p:+.0f}%)" for l, _, p in analysis["peaks"] if l != max_label]
        if other_peaks:
            lines.append(f"Повышенная активность также наблюдается в: {', '.join(other_peaks)}.")
    return " ".join(lines)


# ---------- 2. Сценарий 8: Сегментация и выявление отличий ----------

SEGMENT_PATTERN = re.compile(
    r"\b(сегмент\w*|когорт\w*|кластер\w*|различи\w*|отличи\w*|профил\w*)\b|"
    r"\b(чем\s+отлича\w*|что\s+отлича\w*|группировк\w*\s+по\s+комбинаци\w*|разниц\w*\s+между\s+групп\w*)\b",
    re.IGNORECASE,
)


def is_segmentation(question: str) -> bool:
    """Detect if query asks about segment differences or customer profiling."""
    return bool(SEGMENT_PATTERN.search(question))


def segmentation_summary(columns: list[str], rows: list[list[Any]]) -> str:
    """Summarize multi-attribute or segment profiling differences."""
    if not rows or len(rows) < 2 or len(columns) < 2:
        return ""

    seg_col = columns[0]
    metric_cols = columns[1:]

    lines = [f"Сравнительный анализ сегментов по признаку «{seg_col}» показывает ключевые различия:"]
    for row in rows[:5]:
        seg_name = str(row[0])
        details = []
        for col, val in zip(metric_cols, row[1:]):
            if val is not None:
                try:
                    num = float(str(val).replace(",", ".").replace(" ", ""))
                    details.append(f"{col}: {num:,.0f}".replace(",", " "))
                except (ValueError, TypeError):
                    details.append(f"{col}: {val}")
        lines.append(f"- **{seg_name}**: {'; '.join(details)}.")
    return "\n".join(lines)


# ---------- 3. Сценарий 9: Корреляции и взаимосвязи ----------

CORRELATION_PATTERN = re.compile(
    r"\b(корреляц\w*|взаимосвяз\w*|зависимост\w*|лаг\w*|опережающ\w*|предшеству\w*)\b|"
    r"\b(связан\w*\s+ли|влияет\s+ли|зависит\s+ли)\b",
    re.IGNORECASE,
)


def is_correlation(question: str) -> bool:
    """Detect if query asks about correlation or leading indicators."""
    return bool(CORRELATION_PATTERN.search(question))


def calculate_pearson(x: list[float], y: list[float]) -> float | None:
    """Calculate Pearson correlation coefficient r between two series."""
    n = len(x)
    if n != len(y) or n < 3:
        return None

    mean_x = sum(x) / n
    mean_y = sum(y) / n

    cov = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
    var_x = sum((xi - mean_x) ** 2 for xi in x)
    var_y = sum((yi - mean_y) ** 2 for yi in y)

    denom = math.sqrt(var_x * var_y)
    if denom == 0:
        return 0.0
    return cov / denom


def correlation_summary(r: float, var_x: str, var_y: str) -> str:
    """Generate business interpretation for Pearson correlation."""
    r_abs = abs(r)
    direction = "прямая (положительная)" if r > 0 else "обратная (отрицательная)"

    if r_abs >= 0.7:
        strength = "сильная статистическая взаимосвязь"
    elif r_abs >= 0.4:
        strength = "умеренная статистическая связь"
    elif r_abs >= 0.2:
        strength = "слабая корреляция"
    else:
        strength = "практическое отсутствие линейной корреляции"

    return (
        f"Коэффициент корреляции Пирсона между «{var_x}» и «{var_y}» составляет **r = {r:+.2f}**. "
        f"Это указывает на {strength} ({direction}). "
        f"Обратите внимание: статистическая корреляция отражает совместную динамику, но не гарантирует прямую причинно-следственную зависимость."
    )


# ---------- 4. Сценарий 10: Экстраполяция и «что-если» ----------

FORECAST_PATTERN = re.compile(
    r"\b(прогноз\w*|экстраполяц\w*|ожидает\w*|в\s+будущ\w*|в\s+следующ\w*\s+(квартал\w*|месяц\w*|год\w*))\b|"
    r"\b(что\s+если|как\s+изменит\w*\s+если|при\s+условии\s+что)\b",
    re.IGNORECASE,
)


def is_forecasting(question: str) -> bool:
    """Detect if query asks about trend forecasting, extrapolation or what-if."""
    return bool(FORECAST_PATTERN.search(question))


def linear_extrapolation(
    historical_values: list[float], steps_ahead: int = 3
) -> list[float]:
    """Calculate simple linear trend using least squares and project future steps."""
    n = len(historical_values)
    if n < 2:
        return [historical_values[-1]] * steps_ahead if historical_values else []

    x = list(range(n))
    y = historical_values

    mean_x = sum(x) / n
    mean_y = sum(y) / n

    denom = sum((xi - mean_x) ** 2 for xi in x)
    if denom == 0:
        slope = 0.0
    else:
        slope = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y)) / denom

    intercept = mean_y - slope * mean_x

    projections = [intercept + slope * (n + step) for step in range(steps_ahead)]
    return projections


def forecast_summary(
    historical: list[tuple[str, float]], projected: list[float], unit_label: str = "Период"
) -> str:
    """Generate business explanation for trend extrapolation."""
    if not historical or not projected:
        return ""

    last_actual = historical[-1][1]
    last_proj = projected[-1]
    pct_change = ((last_proj - last_actual) / last_actual) * 100.0 if last_actual else 0.0

    lines = [
        f"На основе экстраполяции исторического тренда ({len(historical)} периодов):",
        f"- Текущее фактическое значение ({historical[-1][0]}): **{last_actual:,.0f}**.".replace(",", " "),
        f"- Прогноз на ближайшие {len(projected)} шага: " + ", ".join(f"{p:,.0f}".replace(",", " ") for p in projected) + ".",
        f"- Ожидаемая динамика к концу прогнозного горизонта: **{pct_change:+.1f}%**.",
        "Примечание: линейный тренд предполагает сохранение текущих внешних факторов и не учитывает форс-мажорные шоки.",
    ]
    return "\n".join(lines)
