"""Deterministic two-period, multi-segment average + volume comparison (#121).

A specific comparison asks for count and an average for each of two periods,
per combination of two category dimensions, plus the percentage change. The
LLM must not replace AVG by SUM, omit counts or group again by calendar month.
The SQL is read-only and only uses columns verified by the active catalog.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable

from . import compare
from .semantics import same_word
from .sql_runner import column_kind


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _terms(s: str) -> list[str]:
    return re.findall(r"[а-яёa-z0-9]+", s.lower().replace("ё", "е"))


def _occurrence(tokens: list[str], name: str) -> tuple[int, int] | None:
    components = [t for t in _terms(name) if len(t) >= 4]
    places = [i for i, word in enumerate(tokens)
              if len(word) >= 4 and any(same_word(word, c) for c in components)]
    if not places:
        return None
    return min(places), len({c for c in components
                             if any(same_word(w, c) for w in tokens)})


def _dimensions(question: str, table: dict) -> tuple[str, str] | None:
    # Only react to explicit grouping clauses, not incidental metric descriptions.
    match = re.search(
        r"\bпо\s+(.+?)(?=\s+за\s+(?:\w+\s+)?20\d{2}|\s+за\s+"
        r"(?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)"
        r"|\s*[,.;]|\s+по\s+каждой\s+комбинации|$)",
        question, re.IGNORECASE)
    if not match:
        return None
    grouping = _terms(match.group(1))
    candidates = []
    for col in table.get("columns") or []:
        if col.get("inferred_type") != "category" or column_kind(col) is not None:
            continue
        occurrence = _occurrence(grouping, col["name"])
        if occurrence is not None:
            first_pos, hits = occurrence
            candidates.append((first_pos, -hits, col["name"]))
    # An extra category in the same clause makes the intended pair ambiguous.
    if len(candidates) != 2:
        return None
    candidates.sort()
    return candidates[0][2], candidates[1][2]


def _average_column(question: str, table: dict) -> str | None:
    # Isolate the expression governed by "средн..." instead of words in later
    # instructions ("количество доставок" must not be interpreted as AVG count).
    m = re.search(r"\bсредн\w*\s+(.+?)(?=\s+по\s+|\s+за\s+|[.,;]|$)",
                  question, re.IGNORECASE)
    if not m:
        return None
    terms = _terms(m.group(1))
    scored = []
    for col in table.get("columns") or []:
        if column_kind(col) not in ("integer", "decimal"):
            continue
        occurrence = _occurrence(terms, col["name"])
        if occurrence:
            _pos, hits = occurrence
            scored.append((hits, col["name"]))
    if not scored:
        return None
    best = max(n for n, _ in scored)
    choices = [name for score, name in scored if score == best]
    return choices[0] if len(choices) == 1 else None


@dataclass(frozen=True)
class MultiSegmentPlan:
    sql: str
    description: str
    dims: tuple[str, str]
    periods: tuple[compare.Period, compare.Period]
    avg_columns: tuple[str, str]

    def answer(self, _columns, rows: list[tuple]) -> str:
        if not rows:
            return "За указанные периоды доставок с выбранными признаками не найдено."
        a, b = self.periods
        comparables = [r for r in rows if r[6] is not None]
        total_a = sum(int(r[2] or 0) for r in rows)
        total_b = sum(int(r[4] or 0) for r in rows)
        message = (
            f"Сравнил {a.label} и {b.label} по комбинациям "
            f"«{self.dims[0]}» × «{self.dims[1]}»: "
            f"{len(rows)} комбинаций, доставок {total_a} и {total_b} соответственно. "
            "По каждой комбинации показаны количество доставок и средняя стоимость "
            "за каждый месяц; изменение рассчитано относительно первого периода. "
            "AVG не учитывает пустую стоимость, а количество доставок считает все "
            "записи с датой доставки в соответствующем периоде."
        )
        if comparables:
            top = max(comparables, key=lambda r: abs(Decimal(str(r[6]))))
            message += (
                f" Наибольшее по модулю относительное изменение: "
                f"«{top[0]} / {top[1]}» — {float(top[6]):+.2f}%."
            )
        else:
            message += (" Сопоставимых средних для расчёта процентного изменения нет; "
                        "пустая или нулевая база не превращается в 0%.")
        return message

    def chart(self, rows: list[tuple]) -> dict | None:
        if not rows or len(rows) > 50:
            return None
        def number(v):
            return float(v) if v is not None else None
        return {
            "type": "bar",
            "title": f"Средняя стоимость: {self.periods[0].label} и {self.periods[1].label}",
            "x_label": " / ".join(self.dims),
            "y_label": "Средняя стоимость, руб.",
            "categories": [f"{r[0]} / {r[1]}" for r in rows],
            "series": [
                {"name": self.periods[0].label, "data": [number(r[3]) for r in rows]},
                {"name": self.periods[1].label, "data": [number(r[5]) for r in rows]},
            ],
        }


def build(question: str, table: dict,
          periods: list[compare.Period], date_column: str | None) -> MultiSegmentPlan | None:
    """Build only an unambiguous count+AVG comparison by two category dimensions."""
    if (len(periods) != 2 or not date_column or
            not re.search(r"сравн\w*", question, re.IGNORECASE) or
            not re.search(r"\bсредн\w*", question, re.IGNORECASE) or
            not re.search(r"количеств\w*|число\s+достав|сколько\s+достав", question, re.IGNORECASE) or
            not re.search(r"процент|%", question, re.IGNORECASE)):
        return None
    dimensions = _dimensions(question, table)
    value = _average_column(question, table)
    if not dimensions or not value:
        return None
    if date_column not in {c["name"] for c in table.get("columns") or []
                           if column_kind(c) in ("date", "timestamp")}:
        return None
    a, b = periods
    d1, d2 = dimensions
    ca, cb = a.condition(date_column), b.condition(date_column)
    n1, n2 = f"Доставок: {a.label}", f"Доставок: {b.label}"
    avg1, avg2 = f"Средняя стоимость: {a.label}, руб.", f"Средняя стоимость: {b.label}, руб."
    sql = (
        "WITH metrics AS (SELECT "
        f"{_q(d1)}, {_q(d2)}, "
        f"COUNT(*) FILTER (WHERE {ca}) AS {_q(n1)}, "
        f"ROUND((AVG({_q(value)}) FILTER (WHERE {ca}))::numeric, 2) AS {_q(avg1)}, "
        f"COUNT(*) FILTER (WHERE {cb}) AS {_q(n2)}, "
        f"ROUND((AVG({_q(value)}) FILTER (WHERE {cb}))::numeric, 2) AS {_q(avg2)} "
        "FROM t "
        f"WHERE ({ca}) OR ({cb}) "
        f"GROUP BY {_q(d1)}, {_q(d2)}) "
        f"SELECT {_q(d1)}, {_q(d2)}, {_q(n1)}, {_q(avg1)}, {_q(n2)}, {_q(avg2)}, "
        f"ROUND(100.0 * ({_q(avg2)} - {_q(avg1)}) / NULLIF({_q(avg1)}, 0), 2) "
        f'AS "Изменение, %" FROM metrics '
        f"ORDER BY {_q(d1)}, {_q(d2)}"
    )
    desc = (
        f"одна строка на сочетание «{d1}» и «{d2}», без GROUP BY по месяцу; "
        f"{a.label}: {ca}; {b.label}: {cb}; "
        f"COUNT(*) — все доставки в периоде; "
        f"AVG(«{value}») — средняя среди непустых стоимостей; "
        f"изменение, % = 100 × ({b.label} − {a.label}) / {a.label}; "
        "если базового среднего нет или оно равно нулю, процент не рассчитывается"
    )
    return MultiSegmentPlan(sql, desc, dimensions, (a, b), (avg1, avg2))
