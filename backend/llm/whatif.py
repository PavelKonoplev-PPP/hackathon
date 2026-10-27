"""What-if (#56) и прогноз (сценарий 10): «что будет, если …» и «прогноз на …».

Подход как везде: модель только пишет SQL базового показателя обычным путём
(answer_question с базовым вопросом), а сценарий считает код:
- если изменяемая величина входит в формулу показателя (скидка, цена, количество),
  код подставляет изменение прямо в SQL и пересчитывает в базе — честные цифры;
- если не входит («расходы на рекламу» → «выручка по рекламе»), расчёт
  пропорциональный, с явной оговоркой о допущении;
- прогноз — помесячный ряд + линейный тренд (advanced_analytics.linear_extrapolation).
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from decimal import Decimal

from .semantics import MONEY, overlap, same_word, words

WHATIF = re.compile(r"что\s+(будет|если|станет|произойд)|как\s+измен\w*[^?]*\bесли\b|"
                    r"\bесли\b[^?]*\b(увелич|вырас|выраст|сниз|уменьш|подня|подним|повыс|упад|"
                    r"сократ|опуст|удво|утро)|при\s+условии|в\s+случае\s+если|сценари",
                    re.IGNORECASE)
FORECAST = re.compile(r"прогноз|экстраполяц|спрогнозир|предскаж|"
                      r"(в|на)\s+следующ\w*\s+(месяц|квартал|год|полгода)|"
                      r"что\s+(будет|ждать)\s+дальше", re.IGNORECASE)

_UP = re.compile(r"увелич|вырас|выраст|подня|подним|повыс|рост|прибав|добав", re.IGNORECASE)
_DOWN = re.compile(r"сниз|сниж|уменьш|упад|паден|сократ|опуст|убав", re.IGNORECASE)
_CHANGE = re.compile(r"(?:на|до)?\s*([+-]?\d+(?:[.,]\d+)?)\s*(процентн\w*\s+пункт\w*|п\.?\s*п\.?|"
                     r"пункт\w*|%|процент\w*|раз\w*)", re.IGNORECASE)
_TIMES = re.compile(r"\b(вдвое|в\s+два\s+раза|удво\w*|втрое|в\s+три\s+раза|утро\w*|вполовину|"
                    r"в\s+два\s+раза\s+меньше)", re.IGNORECASE)
_CONDITION = re.compile(r"[,;]?\s*\b(если|при\s+условии(\s+что)?|в\s+случае(\s+если)?|"
                        r"при(?=\s+\w*(сниж|увелич|рост|паден|повыш|уменьш|сокращ)))\b",
                        re.IGNORECASE)


@dataclass
class Change:
    kind: str        # "pct" (×(1+v/100)), "pp" (+v процентных пунктов), "times" (×v)
    value: float
    text: str        # как изменение звучит в ответе

    def apply_sql(self, column_sql: str) -> str:
        if self.kind == "pp":
            return f"LEAST(100, GREATEST(0, {column_sql} + ({self.value:g})))"
        if self.kind == "times":
            return f"({column_sql} * {self.value:g})"
        return f"({column_sql} * {1 + self.value / 100:g})"

    def apply_number(self, x: float) -> float:
        if self.kind == "times":
            return x * self.value
        if self.kind == "pct":
            return x * (1 + self.value / 100)
        return x  # п.п. к итоговому показателю пропорционально не применить


def is_whatif(question: str) -> bool:
    return bool(WHATIF.search(question)) and parse_change(question) is not None


def is_forecast(question: str) -> bool:
    return bool(FORECAST.search(question)) and not is_whatif(question)


def parse_change(question: str) -> Change | None:
    """«вырастет на 5 п.п.» → pp +5; «снизить цены на 10%» → pct −10; «удвоить» → times 2."""
    m = _TIMES.search(question)
    if m:
        t = m.group(1).lower()
        if "половин" in t or "меньше" in t:
            return Change("times", 0.5, "уменьшится вдвое")
        k = 3.0 if ("тро" in t or "три" in t) else 2.0
        return Change("times", k, "вырастет в 3 раза" if k == 3 else "вырастет вдвое")
    m = _CHANGE.search(question)
    if not m:
        return None
    value = float(m.group(1).replace(",", "."))
    unit = m.group(2).lower()
    tail = question[:m.start()][-40:] + question[m.end():m.end() + 10]
    sign = -1 if (_DOWN.search(tail) and not _UP.search(tail)) or value < 0 else 1
    value = abs(value)
    if unit.startswith("раз"):
        k = value if sign > 0 else 1 / value
        return Change("times", k, f"изменится в {value:g} раза" if sign > 0
                      else f"уменьшится в {value:g} раза")
    word = "вырастет" if sign > 0 else "снизится"
    compact = re.sub(r"[\s.]", "", unit)
    if compact == "пп" or "пункт" in unit:
        return Change("pp", sign * value, f"{word} на {value:g} п.п.")
    return Change("pct", sign * value, f"{word} на {value:g}%")


def split_question(question: str) -> tuple[str, str]:
    """«Что будет с выручкой по каналам, если скидка вырастет на 5 п.п.?» →
    («выручкой по каналам», «скидка вырастет на 5 п.п.»)."""
    m = _CONDITION.search(question)
    if m:
        base, cond = question[:m.start()], question[m.end():]
    else:
        # «Сценарий: скидка снизится на 2 п.п. — какая будет выручка по категориям?»
        tail = re.search(r"\b(как\w*|сколько)\s+(будет|станет|окажется)\s+(.*)$", question, re.IGNORECASE)
        base, cond = (tail.group(3), question[:tail.start()]) if tail else ("", question)
    base = re.sub(r"^\s*(что\s+(будет|станет|произойд\w*)\s*(с|со)?|как\s+измен\w*)\s*",
                  "", base, flags=re.IGNORECASE).strip(" ,?—-:")
    return base, cond.strip(" ?—-:")


def base_question(question: str, columns: list[str]) -> str:
    """Вопрос о текущем значении показателя для обычного конвейера («Посчитай: выручка по каналам»).
    Без явного показателя — выручка (если в таблице есть цена и количество)."""
    from .semantics import money_formula

    base, _ = split_question(question)
    if not words(base):
        base = "выручка" if money_formula(columns) else "количество записей"
    if MONEY.search(base) and any("скидк" in c.lower() for c in columns) and "скидк" not in base.lower():
        base += " с учётом скидки"
    return f"Посчитай: {base}"


_SYNONYMS = [(r"\bцен", "цена"), (r"стоимост", "стоимость"), (r"расход|затрат|бюджет", "расходы"),
             (r"продаж|покуп|спрос|объ[её]м", "количество")]


def driver_column(condition: str, numeric_columns: list[str]) -> str | None:
    """Изменяемая величина: числовая колонка, названная в условии («скидка» → «Скидка, %»).
    При равенстве — колонка, которая с этого слова начинается («Цена до скидки» ≠ «скидка»)."""
    asked = words(condition)
    for pattern, word in _SYNONYMS:
        if re.search(pattern, condition, re.IGNORECASE):
            asked |= {word}
    best = None
    for i, col in enumerate(numeric_columns):
        col_words = words(col)
        hits = overlap(asked, col_words)
        if not hits:
            continue
        head = re.findall(r"[а-яёa-z]+", col.lower())
        starts = bool(head) and any(same_word(q, head[0]) for q in asked)
        score = (hits + 2 * starts, -i)
        if best is None or score > best[0]:
            best = (score, col)
    return best[1] if best else None


def scenario_sql(sql: str, driver: str, change: Change) -> str | None:
    """Подставляет изменение в SELECT базового запроса (WHERE/GROUP BY не трогаем)."""
    m = re.search(r"\bfrom\s+t\b", sql, re.IGNORECASE)
    if not m:
        return None
    select, rest = sql[:m.start()], sql[m.start():]
    quoted = f'"{driver}"'
    if quoted not in select:
        return None
    return select.replace(quoted, change.apply_sql(quoted)) + rest


# ---------- результат ----------

def _num(v):
    if isinstance(v, bool) or v is None:
        return None
    return float(v) if isinstance(v, (int, float, Decimal)) else None


def combine(columns: list[str], base_rows: list[tuple], new_rows: list[tuple] | None,
            change: Change):
    """Колонки «Сейчас», «При сценарии», «Изменение», «Изменение, %» по числовым колонкам базы.
    new_rows=None — пропорциональный пересчёт."""
    metric_i = [i for i in range(len(columns))
                if base_rows and all(_num(r[i]) is not None or r[i] is None for r in base_rows)]
    if not metric_i:
        return None
    m = metric_i[-1]
    labels = [i for i in range(len(columns)) if i not in metric_i]
    out_cols = [columns[i] for i in labels] + ["Сейчас", "При сценарии", "Изменение", "Изменение, %"]
    rows = []
    for k, r in enumerate(base_rows):
        now = _num(r[m])
        if new_rows is not None:
            match = next((n for n in new_rows if all(n[i] == r[i] for i in labels)), None)
            new = _num(match[m]) if match else None
        else:
            new = change.apply_number(now) if now is not None else None
        delta = None if now is None or new is None else new - now
        pct = None if delta is None or not now else delta / abs(now) * 100
        rows.append(tuple(r[i] for i in labels) + (now, new, delta, pct))
    return out_cols, rows, columns[m]


def whatif_summary(metric: str, cols: list[str], rows: list[tuple], driver: str | None,
                   change: Change, proportional: bool, fmt) -> str:
    total_now = sum(r[-4] or 0 for r in rows)
    total_new = sum(r[-3] or 0 for r in rows)
    delta = total_new - total_now
    pct = delta / abs(total_now) * 100 if total_now else 0.0
    sign = "+" if delta > 0 else ""
    what = f"«{driver}» {change.text}" if driver else f"показатель {change.text}"
    pct_txt = f"{sign}{pct:.1f}%".replace(".", ",")
    text = (f"Если {what}, «{metric}» изменится с {fmt(total_now)} до {fmt(total_new)} "
            f"({sign}{fmt(delta)}, {pct_txt}).")
    if len(rows) > 1:
        text += " Разбивка — в таблице и на графике."
    if proportional:
        text += (" Допущение: показатель меняется пропорционально (сохраняется текущая отдача, "
                 "другие факторы неизменны).")
    else:
        text += " Пересчитано по данным с подставленным изменением; остальные величины — как сейчас."
    return text


def whatif_chart(cols: list[str], rows: list[tuple], fmt) -> dict:
    label = cols[0] if len(cols) > 4 else ""
    categories = [fmt(r[0]) for r in rows] if len(cols) > 4 else ["Итого"]
    return {
        "type": "bar",
        "title": "Сейчас и при сценарии",
        "x_label": label,
        "y_label": "",
        "categories": categories,
        "series": [{"name": "Сейчас", "data": [r[-4] for r in rows]},
                   {"name": "При сценарии", "data": [r[-3] for r in rows]}],
    }


# ---------- прогноз ----------

def forecast_steps(question: str) -> int:
    q = question.lower()
    if re.search(r"год", q):
        return 12
    if re.search(r"полгода|полугод", q):
        return 6
    m = re.search(r"(\d+)\s*месяц", q)
    if m:
        return max(1, min(24, int(m.group(1))))
    return 3  # «следующий квартал», «ближайшие месяцы»


def forecast_base_question(question: str) -> str:
    base = re.sub(r"спрогнозир\w*|предскаж\w*|экстраполяц\w*|(какой|каков)?\s*прогноз\w*", " ",
                  question, flags=re.IGNORECASE)
    base = re.sub(r"\b(на|в)\s+(следующ\w*|ближайш\w*)\s+\w+|\bна\s+\d+\s+месяц\w*|"
                  r"что\s+(будет|ждать)\s+дальше", " ", base, flags=re.IGNORECASE)
    base = " ".join(base.split()).strip(" ?.,")
    return f"Посчитай: {base if words(base) else 'выручка'} по месяцам"


def next_month(d) -> dt.date:
    d = d.date() if isinstance(d, dt.datetime) else d
    return dt.date(d.year + (d.month == 12), d.month % 12 + 1, 1)
