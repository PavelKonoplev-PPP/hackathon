"""Deterministic, read-only SQL fallback for well-defined expert BI questions.

These are *formulas*, not canned answers: PostgreSQL calculates all numbers
against the selected real table. The fallback runs only when all LLM attempts
fail, with the matching table's verified column names and parsed date bounds.
"""
from __future__ import annotations

import datetime as dt
import re

from .expert_metrics import (
    _end_of_day, _has, _is_asof_purchase, _is_retry_chain,
    _month_window, _percentile,
)

_MONTH_STEMS = {
    1: "январ", 2: "феврал", 3: "март", 4: "апрел",
    5: r"(?:май|мая|мае)", 6: "июн", 7: "июл",
    8: "август", 9: "сентябр", 10: "октябр",
    11: "ноябр", 12: "декабр",
}


def _comparison_months(question: str) -> tuple[dt.date, dt.date, dt.date] | None:
    """Recognize 'in March 2025 compared to February' (consecutive months)."""
    window = _month_window(question)
    if not window:
        return None
    current = dt.date.fromisoformat(window[0])
    current_end = dt.date.fromisoformat(window[1])
    previous_end = current
    previous_last = current - dt.timedelta(days=1)
    previous_start = dt.date(previous_last.year, previous_last.month, 1)
    pattern = r"\bк\s+" + _MONTH_STEMS[previous_start.month] + r"\w*(?:\s+(\d{4}))?\b"
    match = re.search(pattern, question, re.IGNORECASE)
    if not match or (match.group(1) and int(match.group(1)) != previous_start.year):
        return None
    return previous_start, previous_end, current_end


def expert_sql_recipe(question: str, columns: list[str]) -> str | None:
    """Create safe SQL for a narrowly recognized question; None otherwise."""
    q = question.lower()
    comparison = _comparison_months(q)

    if (comparison and _has(columns, "ID_Аптеки", "ID_Лекарства",
                            "Сколько_Купили", "Дата")
            and "среднедневн" in q and "аптек" in q
            and "без продаж" in q and "вырос" in q):
        start, mid, end = comparison
        days_a = (mid - start).days
        days_b = (end - mid).days
        return f"""
WITH pharmacies AS (
    SELECT COUNT(DISTINCT "ID_Аптеки") AS n FROM t
), monthly AS (
    SELECT "ID_Лекарства" AS drug,
           SUM("Сколько_Купили") FILTER (
               WHERE "Дата" >= DATE '{start}' AND "Дата" < DATE '{mid}'
           ) AS older,
           SUM("Сколько_Купили") FILTER (
               WHERE "Дата" >= DATE '{mid}' AND "Дата" < DATE '{end}'
           ) AS newer
    FROM t GROUP BY "ID_Лекарства"
)
SELECT monthly.drug AS drug,
       ROUND(monthly.older / (pharmacies.n * {days_a}.0), 4) AS previous_daily,
       ROUND(monthly.newer / (pharmacies.n * {days_b}.0), 4) AS current_daily,
       ROUND(100 * (
           (monthly.newer / {days_b}.0) /
           NULLIF(monthly.older / {days_a}.0, 0) - 1
       ), 2) AS growth_pct,
       pharmacies.n AS pharmacy_count
FROM monthly CROSS JOIN pharmacies
WHERE monthly.older IS NOT NULL AND monthly.newer IS NOT NULL
  AND pharmacies.n > 0
ORDER BY growth_pct DESC NULLS LAST, monthly.drug
LIMIT 1
""".strip()

    if (comparison and _has(columns, "Город", "Количество, шт.",
                             "Цена до скидки за единицу, руб.",
                             "Скидка, %", "Дата операции (МСК)")
            and "чист" in q and "выручк" in q and "возврат" in q
            and "вырос" in q):
        start, mid, end = comparison
        metric = (
            'SUM("Количество, шт." * "Цена до скидки за единицу, руб." * '
            '(1 - "Скидка, %" / 100.0))'
        )
        return f"""
WITH monthly AS (
    SELECT "Город" AS city,
           {metric} FILTER (
               WHERE "Дата операции (МСК)" >= TIMESTAMP '{start}'
                 AND "Дата операции (МСК)" < TIMESTAMP '{mid}'
           ) AS older,
           {metric} FILTER (
               WHERE "Дата операции (МСК)" >= TIMESTAMP '{mid}'
                 AND "Дата операции (МСК)" < TIMESTAMP '{end}'
           ) AS newer
    FROM t GROUP BY "Город"
)
SELECT city, ROUND(older, 2) AS previous_revenue,
       ROUND(newer, 2) AS current_revenue,
       ROUND(100 * (newer / NULLIF(older, 0) - 1), 2) AS growth_pct
FROM monthly
WHERE older IS NOT NULL AND newer IS NOT NULL
ORDER BY growth_pct DESC NULLS LAST, city
LIMIT 1
""".strip()

    percentile = _percentile(q)
    window = _month_window(q)
    if percentile == 90 and window and (
        _has(columns, "Группа поддержки", "Создано (МСК)", "Закрыто (МСК)")
        and "времен" in q and "решен" in q
    ):
        start, end = window
        return f"""
WITH durations AS (
    SELECT "Группа поддержки" AS group_name,
           EXTRACT(EPOCH FROM ("Закрыто (МСК)" - "Создано (МСК)")) / 3600 AS hours
    FROM t
    WHERE "Закрыто (МСК)" >= TIMESTAMP '{start}'
      AND "Закрыто (МСК)" < TIMESTAMP '{end}'
)
SELECT group_name, COUNT(*) AS cases,
       ROUND(PERCENTILE_CONT(0.9) WITHIN GROUP
           (ORDER BY hours)::numeric, 2) AS p90_hours
FROM durations
GROUP BY group_name
HAVING COUNT(*) >= 100
ORDER BY p90_hours DESC NULLS LAST, group_name
LIMIT 1
""".strip()

    if percentile == 95 and window and (
        _has(columns, "Перевозчик", "Статус",
             "Обещанный срок (МСК)", "Доставлено (МСК)")
        and "задерж" in q and "вовремя" in q
    ):
        start, end = window
        return f"""
WITH delays AS (
    SELECT "Перевозчик" AS carrier,
           GREATEST(0, EXTRACT(EPOCH FROM (
               "Доставлено (МСК)" - "Обещанный срок (МСК)"
           )) / 3600) AS delay_hours
    FROM t
    WHERE "Статус" = 'Доставлено'
      AND "Доставлено (МСК)" >= TIMESTAMP '{start}'
      AND "Доставлено (МСК)" < TIMESTAMP '{end}'
)
SELECT carrier, COUNT(*) AS deliveries,
       ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP
           (ORDER BY delay_hours)::numeric, 2) AS p95_hours
FROM delays
GROUP BY carrier
ORDER BY p95_hours DESC NULLS LAST, carrier
LIMIT 1
""".strip()

    if _is_asof_purchase(q, columns):
        end = _end_of_day(q)
        if end and "Согласованная сумма, руб." in columns:
            date = f"TIMESTAMP '{end}'"
            return f"""
SELECT "Подразделение" AS department, COUNT(*) AS requests,
       ROUND(SUM("Согласованная сумма, руб."), 2) AS amount
FROM t
WHERE "Согласована (МСК)" < {date}
  AND ("Оплачена (МСК)" IS NULL OR "Оплачена (МСК)" >= {date})
  AND ("Отменена (МСК)" IS NULL OR "Отменена (МСК)" >= {date})
  AND ("Отклонена (МСК)" IS NULL OR "Отклонена (МСК)" >= {date})
GROUP BY "Подразделение"
ORDER BY amount DESC NULLS LAST, department
""".strip()

    if _is_retry_chain(q, columns):
        return """
WITH chains AS (
    SELECT "Задание" AS job, "Код цепочки запусков" AS chain,
           MAX("Номер попытки") AS attempts,
           (ARRAY_AGG("Статус" ORDER BY "Номер попытки" DESC))[1] AS final_status
    FROM t
    GROUP BY "Задание", "Код цепочки запусков"
)
SELECT job, COUNT(*) AS total_chains,
       COUNT(*) FILTER (
           WHERE final_status = 'Успешно' AND attempts > 1
       ) AS retry_success,
       ROUND(100.0 * COUNT(*) FILTER (
           WHERE final_status = 'Успешно' AND attempts > 1
       ) / NULLIF(COUNT(*), 0), 2) AS share_pct,
       ROUND(AVG(attempts) FILTER (
           WHERE final_status = 'Успешно' AND attempts > 1
       ), 2) AS avg_attempts
FROM chains
GROUP BY job
ORDER BY share_pct DESC NULLS LAST, job
""".strip()

    return None
