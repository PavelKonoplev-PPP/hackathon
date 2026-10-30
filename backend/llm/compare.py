"""Smart Compare (issue #29): сравнение периодов и сегментов.

Периоды из вопроса разбирает код («сентябрь 2025», «предыдущий месяц», «2025 год»,
«1 квартал 2025»), границы дат точные. Модель пишет один запрос с колонкой на каждый
период через FILTER, а изменение (абсолютное и в %) считает код — арифметику модели
не доверяем.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from decimal import Decimal

COMPARE = re.compile(r"сравн|по сравнению|относительно|\bпротив\b|\bvs\b|\bили\b.*\bбольше|"
                     r"изменил|разниц|отлича", re.IGNORECASE)

MONTHS = [("январ", 1), ("феврал", 2), ("март", 3), ("апрел", 4), ("ма[йяюе]", 5), ("июн", 6),
          ("июл", 7), ("август", 8), ("сентябр", 9), ("октябр", 10), ("ноябр", 11), ("декабр", 12)]
MONTH_NAMES = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь",
               "октябрь", "ноябрь", "декабрь"]
_MONTH_RE = re.compile(r"\b(" + "|".join(p for p, _ in MONTHS) + r")[а-я]*(?:\s+(20\d{2}))?",
                       re.IGNORECASE)
_QUARTER_RE = re.compile(r"\b([1-4]|iv|i{1,3})\s*-?\s*(?:й|ый|ой)?\s*квартал\w*(?:\s+(20\d{2}))?",
                         re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(20\d{2})\s*(?:год\w*|г\.?)?", re.IGNORECASE)
_PREV = re.compile(r"(предыдущ|прошл)\w*\s+(месяц|квартал|год)\w*", re.IGNORECASE)
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4}


@dataclass(frozen=True)
class Period:
    label: str
    start: dt.date
    end: dt.date  # не включая

    def condition(self, column: str) -> str:
        return (f"\"{column}\" >= '{self.start.isoformat()}' AND "
                f"\"{column}\" < '{self.end.isoformat()}'")


def _month(year: int, month: int) -> Period:
    end = dt.date(year + (month == 12), month % 12 + 1, 1)
    return Period(f"{MONTH_NAMES[month - 1]} {year}", dt.date(year, month, 1), end)


def _quarter(year: int, q: int) -> Period:
    start = dt.date(year, 3 * q - 2, 1)
    end = dt.date(year + (q == 4), (3 * q) % 12 + 1, 1)
    return Period(f"{q} квартал {year}", start, end)


def _year(year: int) -> Period:
    return Period(f"{year} год", dt.date(year, 1, 1), dt.date(year + 1, 1, 1))


def _previous(p: Period, unit: str) -> Period:
    if unit.startswith("месяц"):
        prev_end = p.start
        prev = prev_end - dt.timedelta(days=1)
        return _month(prev.year, prev.month)
    if unit.startswith("квартал"):
        prev = p.start - dt.timedelta(days=1)
        return _quarter(prev.year, (prev.month - 1) // 3 + 1)
    return _year(p.start.year - 1)


def parse_periods(text: str, latest: dt.date | None = None) -> list[Period]:
    """Периоды из вопроса в хронологическом порядке (без повторов).

    Месяц без года — последний такой месяц, не позже последней даты в данных (latest).
    «Предыдущий месяц/квартал/год» — относительно названного периода, а если его нет —
    относительно последнего полного периода в данных."""
    periods: list[Period] = []
    used: list[tuple[int, int]] = []

    def add(p: Period | None, span: tuple[int, int]) -> None:
        if p and p not in periods:
            periods.append(p)
        used.append(span)

    for m in _QUARTER_RE.finditer(text):
        q = m.group(1).lower()
        q = _ROMAN.get(q) or int(q)
        year = int(m.group(2)) if m.group(2) else (latest.year if latest else None)
        add(_quarter(year, q) if year else None, m.span())
    month_matches = list(_MONTH_RE.finditer(text))
    for k, m in enumerate(month_matches):
        month = next(n for p, n in MONTHS if re.match(p, m.group(1), re.IGNORECASE))
        later_year = next((int(x.group(2)) for x in month_matches[k + 1:] if x.group(2)), None)
        if m.group(2):
            year = int(m.group(2))
        elif later_year:  # «сентябрь и август 2025» — год один на оба месяца
            year = later_year
        elif latest:
            year = latest.year if month <= latest.month else latest.year - 1
        else:
            continue
        add(_month(year, month), m.span())
    for m in _YEAR_RE.finditer(text):
        if any(a <= m.start() < b for a, b in used):
            continue  # год уже учтён в «сентябрь 2025»
        add(_year(int(m.group(1))), m.span())

    for m in _PREV.finditer(text):
        unit = m.group(2).lower()
        base = periods[-1] if periods else None
        if base is None and latest:
            # последний полный период в данных
            if unit.startswith("месяц"):
                base = _month(latest.year, latest.month)
            elif unit.startswith("квартал"):
                base = _quarter(latest.year, (latest.month - 1) // 3 + 1)
            else:
                base = _year(latest.year)
            if base not in periods:
                periods.append(base)
        if base is not None:
            prev = _previous(base, unit)
            if prev not in periods:
                periods.append(prev)
    return sorted(periods, key=lambda p: p.start)


def is_compare(question: str) -> bool:
    return bool(COMPARE.search(question))


# ---------- подсказка модели и проверка ----------

def compare_hint(periods: list[Period], date_column: str, metric: str = "COUNT(*)") -> str:
    """metric — выражение показателя (например, формула выручки или SUM("Клики"))."""
    first, second = periods[-2], periods[-1]
    return (
        "\nЭто сравнение двух периодов. Посчитай показатель отдельно для каждого периода "
        "через FILTER, в одном запросе, без WHERE по дате и без группировки по дате. "
        f"Например, для показателя {metric}:\n"
        f"  {metric} FILTER (WHERE {first.condition(date_column)}) AS \"{first.label}\",\n"
        f"  {metric} FILTER (WHERE {second.condition(date_column)}) AS \"{second.label}\"\n"
        "Подставь показатель из вопроса. Если в вопросе есть разбивка (по каналам, по городам) — "
        "добавь эту колонку в SELECT и GROUP BY.\n"
    )


def check_compare_sql(sql: str, periods: list[Period], date_column: str | None = None) -> list[str]:
    problems = []
    outer = re.sub(r"filter\s*\(\s*where[^)]*\)", " ", sql, flags=re.IGNORECASE)  # WHERE внутри FILTER — не то
    where = re.search(r"\bwhere\b(.*?)(\bgroup\s+by\b|\border\s+by\b|\blimit\b|$)", outer,
                      re.IGNORECASE | re.DOTALL)
    if date_column and where and f'"{date_column}"' in where.group(1):
        problems.append(f"Убери условие по \"{date_column}\" из WHERE: периоды задаются "
                        "только внутри FILTER (...) у каждой колонки.")
    for p in periods[-2:]:
        if p.start.isoformat() not in sql or p.end.isoformat() not in sql:
            problems.append(f"Нужен отдельный показатель за {p.label}: FILTER (WHERE дата >= "
                            f"'{p.start.isoformat()}' AND дата < '{p.end.isoformat()}') "
                            f"AS \"{p.label}\".")
    problems += _check_period_columns(sql)
    return problems


def _strip_filter(item: str) -> str:
    """Выражение без «FILTER (…)» (скобки внутри названий колонок учитываются)."""
    m = re.search(r"\bfilter\s*\(", item, re.IGNORECASE)
    if not m:
        return item
    depth, i = 1, m.end()
    while i < len(item) and depth:
        depth += {"(": 1, ")": -1}.get(item[i], 0)
        i += 1
    return (item[:m.start()] + item[i:]).strip()


def _check_period_columns(sql: str) -> list[str]:
    """#97: ровно два показателя — один и тот же за каждый период через FILTER; никаких
    процентов, долей и изменений в SQL (их считает код, иначе сравниваются % с рублями)."""
    from .kpis import _AGGREGATE, _norm, select_items
    aggregates = [it for it in select_items(sql) if _AGGREGATE.search(it)]
    if not aggregates:
        return []
    without_period = [it for it in aggregates if not re.search(r"\bfilter\s*\(", it, re.IGNORECASE)]
    bases = {_norm(_strip_filter(it)) for it in aggregates}
    if without_period or len(aggregates) != 2 or len(bases) != 1:
        return ["Для сравнения верни ровно две колонки — один и тот же показатель за каждый период "
                "через FILTER (с псевдонимами-периодами). Не добавляй проценты, доли и изменения: "
                "их считает код."]
    return []


def choose_date_column(table: dict, question: str, kinds: dict[str, str]) -> str | None:
    """Колонка даты для сравнения: упомянутая в вопросе, иначе первая без пропусков."""
    dates = [c for c in table.get("columns") or [] if kinds.get(c["name"]) in ("date", "timestamp")]
    if not dates:
        return None
    low = question.lower()
    for c in dates:
        stem = c["name"].lower().split()[0][:5]
        if stem in low:
            return c["name"]
    complete = [c for c in dates if (c.get("conversion") or {}).get("policy") == "safe_complete"]
    return (complete or dates)[0]["name"]


# ---------- результат сравнения ----------

def is_percent_metric(name: str) -> bool:
    """Check if the column or metric is measured in percent (%, CTR, discount, share)."""
    low = name.lower()
    return "%" in name or any(w in low for w in ("процент", "доля", "ctr", "скидк", "конверси", "ставка"))


def is_money_metric(name: str) -> bool:
    """Check if the column or metric is monetary (RUB, price, revenue, spend)."""
    low = name.lower()
    return any(w in low for w in ("руб", "₽", "выручк", "цена", "расход", "бюджет", "сумм", "стоимост", "доход"))


def are_compatible_metrics(name_a: str, name_b: str) -> bool:
    """Validate that two columns or labels have compatible units and can be compared.

    Prevents comparing percentages with rubles or other unit mismatches (issue #97).
    """
    str_a, str_b = str(name_a).strip(), str(name_b).strip()
    pct_a, pct_b = is_percent_metric(str_a), is_percent_metric(str_b)
    if pct_a != pct_b:
        return False
    money_a, money_b = is_money_metric(str_a), is_money_metric(str_b)
    if money_a != money_b:
        return False
    return True


def _num(v) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float, Decimal)):
        return float(v)
    return None


def enrich_compare(columns: list[str], rows: list[tuple], periods: list[Period]):
    """Добавляет «Изменение» и «Изменение, %». Возвращает (колонки, строки, сводка) или None."""
    labels = [p.label for p in periods[-2:]]
    idx = [columns.index(l) for l in labels if l in columns]
    if len(idx) != 2:
        numeric = [i for i in range(len(columns))
                   if rows and all(_num(r[i]) is not None or r[i] is None for r in rows)]
        if len(numeric) < 2:
            return None
        candidate = numeric[-2:]
        if not are_compatible_metrics(columns[candidate[0]], columns[candidate[1]]):
            return None
        idx = candidate
    a, b = idx
    is_pct = is_percent_metric(columns[a]) or is_percent_metric(columns[b])
    out_rows = []
    for r in rows:
        x, y = _num(r[a]), _num(r[b])
        delta = None if x is None or y is None else y - x
        if is_pct:
            # Разница двух процентов измеряется строго в п.п., относительный % от % не вычисляется (#97)
            out_rows.append(tuple(r) + (delta, None))
        else:
            pct = None if delta is None or not x else delta / abs(x) * 100
            out_rows.append(tuple(r) + (delta, pct))
    change_col = "Изменение, п.п." if is_pct else "Изменение"
    out_cols = list(columns) + [change_col, "Изменение, %"]
    return out_cols, out_rows, (a, b)


def compare_summary(columns: list[str], rows: list[tuple], pair: tuple[int, int],
                    fmt) -> str:
    """Текст вывода: одна строка — итог; несколько — лидеры роста и падения."""
    a, b = pair
    first, second = columns[a], columns[b]
    is_pct = is_percent_metric(first) or is_percent_metric(second)
    labels = [i for i in range(len(columns) - 2) if i not in pair and _num(rows[0][i]) is None]

    def change(r):
        delta, pct = r[-2], r[-1]
        if delta is None:
            return "нет данных для сравнения"
        sign = "+" if delta > 0 else ""
        word = "рост" if delta > 0 else ("снижение" if delta < 0 else "без изменений")
        if is_pct:
            diff_str = f"{sign}{delta:.1f}".replace(".", ",")
            return f"{word}: {diff_str} п.п."
        pct_txt = f", {sign}{pct:.1f}%".replace(".", ",") if pct is not None else ""
        return f"{word}: {sign}{fmt(delta)}{pct_txt}"

    if len(rows) == 1 or not labels:
        r = rows[0]
        return f"{second}: {fmt(r[b])}, {first}: {fmt(r[a])} — {change(r)}."
    name = labels[0]
    valid = [r for r in rows if r[-2] is not None]
    if not valid:
        return "Для сравнения не хватает данных за один из периодов."
    up = max(valid, key=lambda r: r[-2] if is_pct else (r[-1] if r[-1] is not None else r[-2]))
    down = min(valid, key=lambda r: r[-2] if is_pct else (r[-1] if r[-1] is not None else r[-2]))
    total_a = sum(_num(r[a]) or 0 for r in rows)
    total_b = sum(_num(r[b]) or 0 for r in rows)
    total_delta = total_b - total_a
    total_pct = (total_delta / abs(total_a) * 100 if total_a else None) if not is_pct else None
    total = change((None,) * 0 + (total_delta, total_pct))
    text = (f"{second} к {first} в целом — {total}. "
            f"Сильнее всего выросло «{fmt(up[name])}» ({change(up)})")
    if down is not up:
        text += f", слабее всего — «{fmt(down[name])}» ({change(down)})"
    return text + "."


def compare_chart(columns: list[str], rows: list[tuple], pair: tuple[int, int], fmt) -> dict:
    a, b = pair
    labels = [i for i in range(len(columns) - 2) if i not in pair and _num(rows[0][i]) is None]
    categories = [fmt(r[labels[0]]) for r in rows] if labels else ["Итого"]
    return {
        "type": "bar",
        "title": f"{columns[b]} и {columns[a]}",
        "x_label": columns[labels[0]] if labels else "",
        "y_label": "",
        "categories": categories,
        "series": [{"name": columns[i], "data": [_num(r[i]) for r in rows]} for i in (a, b)],
    }
