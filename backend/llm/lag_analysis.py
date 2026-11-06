"""Verified daily lead/lag correlations (#118), built as read-only SQL.

Only two explicitly named verified numeric columns from ONE catalog table and
one verified date column are eligible. Pairs join on real calendar dates, not on
row positions; missing days are not imputed. A positive lag means X at day t
precedes Y at t+lag. SQL Pearson CORR excludes constant/short series.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from . import compare
from .semantics import same_word
from .sql_runner import column_kind


_LAG_INTENT = re.compile(
    r"\bлаг\w*|временн\w*\s+сдвиг\w*|сдвиг\w*\s+во\s+времени|"
    r"опережа\w*|предшеству\w*|запаздыва\w*|"
    r"отста[её]т\s+на\s+\d+\s+дн",
    re.IGNORECASE,
)
_RANGE = re.compile(
    r"(?:от\s*)?([+\-−]?\d+)\s*(?:до|по)\s*([+\-−]?\d+)\s*(?:дн|дня|дней|сут)",
    re.IGNORECASE,
)
_PLUS_MINUS = re.compile(r"(?:±|\+/-)\s*(\d+)\s*(?:дн|дня|дней|сут)", re.IGNORECASE)
_DAYS = re.compile(r"(\d+)\s*(?:дн|дня|дней|сут)", re.IGNORECASE)
_MAX_LAG_DAYS = 30
_MIN_PAIRS = 5


def is_lag_request(question: str) -> bool:
    return _LAG_INTENT.search(question) is not None


def _bounds(question: str) -> tuple[int, int] | None:
    """Explicit signed range or ±N days, or a symmetric bounded search.

    None means a requested range is invalid (too wide or reversed). Otherwise
    default to -7..+7 days if the user did not give a number.
    """
    def n(value: str) -> int:
        return int(value.replace("−", "-"))
    m = _RANGE.search(question)
    if m:
        lo, hi = n(m.group(1)), n(m.group(2))
    else:
        m = _PLUS_MINUS.search(question)
        if m:
            lo, hi = -int(m.group(1)), int(m.group(1))
        else:
            m = _DAYS.search(question)
            if m:
                # A "5 day" lag does not specify the direction; search both.
                lo, hi = -int(m.group(1)), int(m.group(1))
            else:
                lo, hi = -7, 7
    if lo > hi or max(abs(lo), abs(hi)) > _MAX_LAG_DAYS:
        return None
    return lo, hi


def _terms(value: str) -> list[str]:
    return re.findall(r"[а-яёa-z0-9]+", value.lower().replace("ё", "е"))


def _metrics(question: str, table: dict) -> tuple[str, str] | None:
    """Find exactly two explicitly named numeric metrics in mention order.

    In a natural question like "lag between clicks and revenue. Show the
    coefficient and number of paired days", *show* and *number* are output
    instructions, not a third metric. Prefer the "между X и Y" clause and
    inspect the rest only if that clause is absent. Never guess a metric.
    """
    from .semantics import STOP_WORDS

    between = re.search(r"\bмежду\b", question, re.IGNORECASE)
    if between:
        relation = re.split(r"[.!?;\n]", question[between.end():], maxsplit=1)[0]
        # Keep only the metric clause; trailing asks about statistics, lags,
        # sample size and display format are not additional series.
        relation = re.split(
            r"\b(?:от\s*[+\-−]?\d+\s*до|на\s*\d+\s*дн|"
            r"покажи|покажите|выведи|коэффициент|число\s+пар|"
            r"количество\s+сопоставленных)\b",
            relation, maxsplit=1, flags=re.IGNORECASE
        )[0]
        search_text = relation
    else:
        search_text = question
    asked = [w for w in _terms(search_text) if w not in STOP_WORDS]
    found = []
    for col in table.get("columns") or []:
        if column_kind(col) not in ("integer", "decimal"):
            continue
        terms = [w for w in _terms(col["name"]) if len(w) >= 4]
        occurrences = [i for i, tok in enumerate(asked)
                       if len(tok) >= 4 and any(same_word(tok, term) for term in terms)]
        if occurrences:
            found.append((min(occurrences), col["name"]))
    found.sort(key=lambda x: x[0])
    if len(found) != 2 or found[0][1] == found[1][1]:
        return None
    return found[0][1], found[1][1]


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


@dataclass(frozen=True)
class LagPlan:
    sql: str
    description: str
    answer: Callable


def build_lag_plan(question: str, table: dict) -> LagPlan | None:
    """Make date-aligned Pearson lag search, never a fabricated LLM statistic."""
    if not is_lag_request(question):
        return None
    metrics = _metrics(question, table)
    bounds = _bounds(question)
    if not metrics or bounds is None:
        return None
    cols = {c["name"]: column_kind(c) for c in table.get("columns") or []}
    date_col = compare.choose_date_column(table, question, cols)
    if not date_col:
        return None
    periods = compare.parse_periods(question)
    if len(periods) > 1:
        return None
    period = periods[0] if periods else None
    source, target = metrics
    lo, hi = bounds

    date_expr = f"date_trunc('day', {_q(date_col)})::date"
    where = f" WHERE {_q(date_col)} IS NOT NULL"
    if period:
        where += f" AND ({period.condition(date_col)})"
    steps = " UNION ALL ".join(
        f"SELECT {lag}::integer" + (" AS lag_days" if lag == lo else "")
        for lag in range(lo, hi + 1)
    )
    sql = (
        f"WITH daily AS ("
        f"SELECT {date_expr} AS day, "
        f"SUM({_q(source)})::double precision AS x, "
        f"SUM({_q(target)})::double precision AS y "
        f"FROM t{where} GROUP BY 1"
        f"), shifts AS ({steps}), "
        f"pairs AS ("
        f"SELECT shifts.lag_days, a.x, b.y "
        f"FROM shifts JOIN daily a ON TRUE "
        f"JOIN daily b ON b.day = a.day + shifts.lag_days "
        f"WHERE a.x IS NOT NULL AND b.y IS NOT NULL"
        f"), stats AS ("
        f"SELECT lag_days, COUNT(*) AS n, CORR(x, y) AS r "
        f"FROM pairs GROUP BY lag_days"
        f") "
        f'SELECT lag_days AS "Лаг, дней", n AS "Пар наблюдений", '
        f'ROUND(r::numeric, 4) AS "Корреляция r" '
        f"FROM stats WHERE n >= {_MIN_PAIRS} AND r IS NOT NULL "
        f"ORDER BY abs(r) DESC, n DESC, abs(lag_days) ASC"
    )
    description = (
        f"дневные суммы «{source}» и «{target}» по «{date_col}»; "
        f"поиск сдвигов от {lo:+d} до {hi:+d} дней; "
        "r Пирсона по реально совпадающим календарным датам, "
        "без заполнения отсутствующих дней; "
        f"минимум {_MIN_PAIRS} пар, постоянные ряды исключены"
        + (f"; период обеих серий — {period.label}" if period else "")
    )

    def answer(_columns, rows) -> str:
        if not rows:
            return (
                f"Невозможно надёжно оценить лаг между «{source}» и «{target}»: "
                f"не нашлось сдвигов с минимум {_MIN_PAIRS} парами наблюдений "
                "и ненулевой изменчивостью обоих рядов. "
                "Пропущенные даты не дополнялись искусственными значениями."
            )
        lag, n, r = rows[0]
        lag, n, r = int(lag), int(n), float(r)
        if lag > 0:
            direction = (f"«{source}» предшествует «{target}» "
                         f"на {lag} дн.")
        elif lag < 0:
            direction = (f"«{target}» предшествует «{source}» "
                         f"на {-lag} дн.")
        else:
            direction = "Сдвига между календарными днями нет."
        return (
            f"Сильнейшая по модулю измеренная корреляция между «{source}» и "
            f"«{target}» на лаге {lag:+d} дней: r = {r:+.4f}, "
            f"пар наблюдений: {n}. {direction} "
            f"Методика: {description}. "
            "Это поисковый анализ нескольких лагов; корреляция не "
            "подтверждает причинно-следственную связь и может отражать общий тренд."
        )

    return LagPlan(sql=sql, description=description, answer=answer)
