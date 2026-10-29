"""Сценарий 2 (issue #4): ответ на вопрос по данным.

Шаги (каждый — короткий запрос, чтобы справлялась и маленькая модель):
0. Контекст диалога (по session_id): ответ на уточнение, продолжение прошлого
   вопроса («а теперь только за сентябрь»), отказ от изменения данных.
1. Выбор таблицы кодом: слова вопроса сравниваются с названиями таблиц, колонок
   и значениями категорий. Ничья — уточняющий вопрос; ничего общего — вежливый отказ.
2. Неоднозначная единица счёта («сколько продаж?» — строк или заказов?) — уточнение.
3. SQL: модель видит колонки выбранной таблицы `t` с типами и значениями категорий.
4. Проверка безопасности (sql_guard) и смысла (semantics): «сколько» — COUNT/SUM,
   «разных» — DISTINCT, LIMIT только для топа, фильтры только из вопроса.
   Ошибку модель получает обратно и исправляет запрос (до 3 попыток).
5. Объяснение: модель пересказывает результат (числа уже отформатированы кодом).

Запуск из корня проекта (нужна запущенная база и Ollama):
    python -m backend.llm.ask "Какой канал продаж приносит больше всего выручки?"
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from decimal import Decimal
from pathlib import Path

from collections import OrderedDict

from . import advanced_analytics as adv
from . import compare as cmp
from . import glossary as gl
from . import kpis as kp
from . import whatif as wi
from .catalog_summary import TYPE_LABELS, display_table_name, format_int, match_table_name
from .semantics import (DEFAULT_DATASET_SUGGESTIONS, MONEY, MUTATION_PATTERN, Intent,
                        adjust_sql, check_destructive_mutation, check_money, check_sql,
                        counted_noun, detect_ambiguous_query, detect_out_of_scope,
                        is_pure_view_request, money_formula, overlap, requested_view,
                        same_word, strip_unmentioned_filters, strong_word, words)
from .sql_guard import SQLRejected, extract_sql, validate_sql
from .sql_runner import column_kind

MAX_ATTEMPTS = 3
EXPLAIN_ROWS = 20

# Просьбы изменить данные — повелительные формы («удали», «измени»), но не «изменилось».
WRITE_INTENT = MUTATION_PATTERN

ANALYTIC_WORDS = re.compile(r"сколько|средн|сумм|доля|процент|динамик|\bтоп|самы|количеств|"
                            r"сравн|рост|паден|выручк|расход|итог|посчитай|распределен|"
                            r"по месяцам|по годам|больше всего|меньше всего|чаще|реже|"
                            r"\bпо\s+\w+(ам|ям)\b|в каждом|в каждой", re.IGNORECASE)
FOLLOWUP_START = re.compile(r"^\s*(а|и|теперь|только|то же|тоже|также|так же|сравни|"
                            r"а теперь|а если|а за|а по|а для|а в|а без|а с|ещё|еще)\b",
                            re.IGNORECASE)
FOLLOWUP_WORDS = re.compile(r"\b(это|этот|эти|этого|то же|те же|то же самое|предыдущ\w*|"
                            r"прошл\w*\s+(запрос|вопрос))\b", re.IGNORECASE)

SQL_PROMPT = """Ты пишешь SQL для PostgreSQL 16. Все данные — в одной таблице t.

Колонки таблицы t (названия пиши ТОЧНО так и ВСЕГДА в двойных кавычках):
{columns}

Правила:
- Ровно один запрос SELECT ... FROM t. Никаких других таблиц и команд.
- Числа и даты в t уже нужного типа: не преобразуй их и не меняй запятые.
- «Сколько» — это всегда агрегат: COUNT(*) для числа строк/операций,
  SUM("колонка") для штук и денег. Никогда не отвечай списком строк.
- «Разных», «уникальных» X — COUNT(DISTINCT "X").
- WHERE добавляй только для условий, которые есть в вопросе. Значения категорий
  бери только из списка, написание — точно как в списке.
- LIMIT только если просят «топ», «самые», «первые N». Иначе выведи все группы.
- По месяцам: GROUP BY date_trunc('month', "дата") и ORDER BY по этой дате;
  по годам — date_trunc('year', ...).
- Разница во времени в часах: EXTRACT(EPOCH FROM ("конец" - "начало")) / 3600.
- Доля в процентах: 100.0 * COUNT(*) FILTER (WHERE условие) / COUNT(*).
- Сумма денег (выручка, оборот) = количество × цена за единицу; если есть скидка
  в процентах — умножай ещё на (1 - скидка / 100.0).
- Давай результатам понятные русские имена: AS "Выручка, руб.".
- Ответь только SQL в блоке ```sql ... ```, без пояснений.
{hint}
Пример. Вопрос: «{example_q}»
```sql
{example_sql}
```"""

EXPLAIN_PROMPT = """Ты — аналитик. Ответь на вопрос пользователя по результату запроса к данным.
Правила:
- 1–3 предложения по-русски, простыми словами, без SQL.
- Начни сразу с ответа (без слов «Ответ:», «Прямой ответ:»), затем 1–2 ключевые цифры.
- Бери числа ТОЛЬКО из результата и пиши их так же, как в нём. Ничего не вычисляй сам.
- Если результат неполный (показаны не все строки) — не делай выводов о «всех»."""


# ---------- подготовка промптов ----------

def _column_line(col: dict, values: dict[str, list[str]]) -> str:
    kind = column_kind(col)
    if kind:
        label = TYPE_LABELS[kind]
    elif col.get("inferred_type") in ("identifier",):
        label = "код (текст)"
    elif col.get("inferred_type") == "category":
        label = "категория (текст)"
    else:
        label = "текст"
    line = f'- "{col["name"]}" — {label}'
    if col["name"] in values:
        line += "; значения: " + ", ".join(f"'{v}'" for v in values[col["name"]])
    return line + gl.note(col)  # подтверждённое определение из бизнес-словаря (#62)


def _example(table: dict) -> tuple[str, str]:
    """Пример под конкретную таблицу: число строк по первой категории."""
    for col in table.get("columns") or []:
        if col.get("inferred_type") == "category":
            name = col["name"]
            return (f"Сколько записей по каждому значению «{name}»?",
                    f'SELECT "{name}", COUNT(*) AS "Количество"\nFROM t\n'
                    f'GROUP BY "{name}"\nORDER BY "Количество" DESC')
    return "Сколько всего записей?", 'SELECT COUNT(*) AS "Количество" FROM t'


def build_sql_messages(question: str, table: dict, values: dict[str, list[str]],
                       history: list[dict] | None = None,
                       previous: dict | None = None, extra_hint: str = "") -> list[dict]:
    """previous — прошлый вопрос и SQL в этом диалоге, если новый вопрос его уточняет.
    extra_hint — дополнительная инструкция (например, границы периодов для сравнения)."""
    columns = "\n".join(_column_line(c, values) for c in table.get("columns") or [])
    example_q, example_sql = _example(table)
    mentioned = mentioned_columns(question, table)
    hint = ("\nПодсказка: в вопросе, вероятно, речь о колонках "
            + ", ".join(f'"{c}"' for c in mentioned) + ".\n") if mentioned else ""
    system = SQL_PROMPT.format(columns=columns, example_q=example_q,
                               example_sql=example_sql, hint=hint + extra_hint)
    messages = [{"role": "system", "content": system}]
    if previous:
        messages += [
            {"role": "user", "content": f"Вопрос: {previous['question']}"},
            {"role": "assistant", "content": f"```sql\n{previous['sql']}\n```"},
            {"role": "user", "content": f"Уточнение к предыдущему вопросу: {question}\n"
                                        "Измени предыдущий запрос: сохрани показатель и "
                                        "группировку, поменяй только то, о чём просят."},
        ]
    else:
        messages.append({"role": "user", "content": f"Вопрос: {question}"})
    return messages + list(history or [])


def mentioned_columns(question: str, table: dict, limit: int = 3) -> list[str]:
    """До трёх колонок, лучше всего совпадающих со словами вопроса (подсказка модели).
    Коды/ID и даты-события, совпавшие одним словом, не предлагаем — они чаще шумят;
    но в вопросе «уникальных покупателей» код покупателя — как раз то, что нужно."""
    distinct = Intent.parse(question).wants_distinct
    table_words = words(display_table_name(table["name"]))
    # слова из названия таблицы («продаж» в «Продажах») не отличают колонки друг от друга
    asked = {q for q in words(question) if not any(same_word(q, w) for w in table_words)}
    scored = []
    for i, col in enumerate(table.get("columns") or []):
        hits = overlap(asked, gl.column_words(col))  # название + синонимы из словаря
        if not hits:
            continue
        kind = col.get("inferred_type")
        if distinct and kind == "identifier":
            scored.append((-hits - 1, i, col["name"]))  # идентификатор — вперёд
            continue
        noisy = kind in ("identifier", "date", "timestamp")
        if noisy and hits < 2:
            continue
        scored.append((-hits, i, col["name"]))
    return [name for _, _, name in sorted(scored)[:limit]]


def keyword_scores(question: str, tables: list[dict],
                   values_by_table: dict[str, dict[str, list[str]]] | None = None,
                   detail: list | None = None) -> list[int]:
    """Насколько вопрос похож на таблицу. Каждое слово вопроса считается один раз:
    совпало с названием таблицы — 2 очка, с колонкой или значением категории — 1.
    detail (необязательно) получает для каждой таблицы число надёжных совпадений с
    названием таблицы или колонок (почти та же словоформа) — для разрешения ничьих."""
    asked = words(question)
    approved_kpis = kp.load()
    scores = []
    for t in tables:
        name_words = words(display_table_name(t["name"]))
        col_words = set().union(set(), *(gl.column_words(c) for c in t.get("columns") or []))
        if money_formula([c["name"] for c in t.get("columns") or []]):
            col_words |= {"выручка", "оборот"}  # выручку можно посчитать: количество × цена
        values = (values_by_table or {}).get(t["name"]) or {}
        # длинные описательные значения («Недостаточно прав для выполнения операции») шумят
        value_words = set().union(set(), *(words(v) for vs in values.values() for v in vs
                                           if len(v.split()) <= 3))
        score = structural = 0
        for q in asked:
            if any(same_word(q, w) for w in name_words):
                score += 2
                structural += 1
            elif any(same_word(q, w) for w in col_words):
                score += 1
                structural += any(strong_word(q, w) for w in col_words)
            elif any(same_word(q, w) for w in value_words):
                score += 1
        bonus = kp.table_bonus(question, t, None, approved_kpis)  # утверждённый KPI этой таблицы
        scores.append(score + bonus)
        if detail is not None:
            detail.append(structural + (1 if bonus else 0))
    return scores


def choose_table(question: str, tables: list[dict], values_by_table: dict) -> tuple[dict | None, list[dict]]:
    """(таблица, []) — выбрана; (None, кандидаты) — нужна помощь пользователя.
    Кандидатов нет — вопрос не про эти данные."""
    if not tables:
        return None, []
    structural: list[int] = []
    scores = keyword_scores(question, tables, values_by_table, structural)
    order = sorted(range(len(tables)), key=lambda i: -scores[i])
    best = scores[order[0]]
    analytic = bool(ANALYTIC_WORDS.search(question) or wi.WHATIF.search(question)
                    or wi.FORECAST.search(question))
    # «Какая погода в Москве?» — совпало только значение «Москва», а вопрос не аналитический
    if best == 0 or (not analytic and max(structural) == 0):
        if len(tables) == 1:
            if analytic or best > 0:
                return tables[0], []
            return None, []
        if analytic:
            return None, tables  # аналитический вопрос без зацепок — спросим, о каких данных
        return None, []
    matched = [i for i in order if scores[i] > 0]
    if len(words(question)) <= 1 and len(matched) > 1 and best < 2:
        return None, [tables[i] for i in matched[:4]]  # одно слово, несколько таблиц — мало данных
    tied = [i for i in order if scores[i] == best]
    if len(tied) > 1:  # при равенстве важнее совпадения с названиями колонок, а не значениями
        top_structural = max(structural[i] for i in tied)
        tied = [i for i in tied if structural[i] == top_structural]
    if len(tied) == 1:
        return tables[tied[0]], []
    return None, [tables[i] for i in tied[:4]]


def unit_options(question: str, intent: Intent, table: dict) -> list[tuple[str, str]]:
    """«Сколько продаж?», когда строка таблицы — позиция заказа: считать строки или
    уникальные заказы? Возвращает варианты (текст кнопки, уточнение к вопросу) или [].

    Уточняем, только если у таблицы есть уникальный код строки, который НЕ совпадает
    со считаемым словом («Код позиции» ≠ «продаж»), и есть «групповой» код с высокой
    кардинальностью («Код заказа»). «Сколько событий» при «Код события» — однозначно строки."""
    if not intent.is_count or intent.has_unit or intent.wants_distinct:
        return []
    noun = counted_noun(question)
    table_words = words(display_table_name(table["name"]))
    if not noun or not any(same_word(noun, w) for w in table_words):
        return []
    ids = [c for c in table.get("columns") or []
           if c.get("inferred_type") == "identifier" and not c.get("warning") and c.get("sample_rows")]
    unique = [c for c in ids if (c.get("sample_distinct") or 0) >= c["sample_rows"]]
    if not unique or any(same_word(noun, w) for c in unique for w in words(c["name"])):
        return []
    groups = [c for c in ids if 0.5 * c["sample_rows"] <= (c.get("sample_distinct") or 0) < c["sample_rows"]]
    groups = sorted(groups, key=lambda c: -(c.get("sample_distinct") or 0))[:2]
    if not groups:
        return []
    return ([("Количество строк (записей)", "считать количество строк")]
            + [(f"Количество уникальных: {c['name']}", f"считать количество уникальных «{c['name']}»")
               for c in groups])


# ---------- форматирование результата ----------

def format_value(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, int):
        return format_int(value)
    if isinstance(value, (Decimal, float)):
        number = float(value)
        if number.is_integer():
            return format_int(int(number))
        return f"{number:,.2f}".replace(",", " ").replace(".", ",")
    if isinstance(value, dt.datetime):
        if value.time() == dt.time(0):
            return value.strftime("%d.%m.%Y")
        return value.strftime("%d.%m.%Y %H:%M")
    if isinstance(value, dt.date):
        return value.strftime("%d.%m.%Y")
    return str(value)


def _is_number(value) -> bool:
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)


VIEW_NAMES = {"bar": "столбчатой диаграммой", "line": "линейным графиком",
              "pie": "круговой диаграммой", "table": "таблицей"}
SHARE_WORDS = re.compile(r"доля|долю|доли|структур|соотношен|распределен|процент", re.IGNORECASE)
PIE_MAX = 12


def suggest_chart(columns: list[str], rows: list[tuple], preferred: str | None = None,
                  question: str = "") -> tuple[dict | None, str]:
    """График по результату: первая колонка — подписи, остальные числовые — серии.

    preferred — вид, о котором попросил пользователь (bar / line / pie / table).
    Без просьбы: время — линия, «доля/структура» при немногих частях — круг, иначе столбцы.
    Возвращает (график или None, пояснение, если просьбу выполнить нельзя)."""
    if preferred == "table":
        return None, ""
    pivot = _pivot(columns, rows)
    if pivot:
        return _pivot_chart(pivot, preferred)
    if len(columns) < 2 or not 2 <= len(rows) <= 50:
        return None, ("Для графика нужно от 2 до 50 значений — показываю таблицей."
                      if preferred else "")
    numeric = [i for i in range(1, len(columns))
               if all(_is_number(r[i]) or r[i] is None for r in rows)]
    if not numeric or len(numeric) != len(columns) - 1:
        return None, ("Этот результат нельзя нарисовать графиком — показываю таблицей."
                      if preferred else "")
    is_time = all(isinstance(r[0], (dt.date, dt.datetime)) for r in rows)
    if is_time:
        rows = sorted(rows, key=lambda r: r[0])
    pie_ok = (len(numeric) == 1 and len(rows) <= PIE_MAX
              and all(r[numeric[0]] is None or r[numeric[0]] >= 0 for r in rows))

    note = ""
    if preferred == "pie" and not pie_ok:
        kind, note = ("line" if is_time else "bar"), (
            f"Круговая диаграмма подходит для одной величины и не более {PIE_MAX} частей — "
            f"показываю {VIEW_NAMES['line' if is_time else 'bar']}.")
    elif preferred in ("bar", "line", "pie"):
        kind = preferred
    elif is_time:
        kind = "line"
    elif pie_ok and len(rows) <= 8 and SHARE_WORDS.search(question):
        kind = "pie"
    else:
        kind = "bar"
    chart = {
        "type": kind,
        "title": columns[numeric[0]] if len(numeric) == 1 else "",
        "x_label": columns[0],
        "y_label": columns[numeric[0]] if len(numeric) == 1 else "",
        "categories": [format_value(r[0]) for r in rows],
        "series": [{"name": columns[i],
                    "data": [float(r[i]) if r[i] is not None else None for r in rows]}
                   for i in numeric],
    }
    return chart, note


def _pivot(columns: list[str], rows: list[tuple]) -> dict | None:
    """Три колонки «разрез × разрез → число» (канал, месяц, выручка) — для графика
    с несколькими сериями: по оси X — время или второй разрез, серия на каждое значение."""
    if len(columns) != 3 or not rows:
        return None
    kinds = []
    for i in range(3):
        col = [r[i] for r in rows if r[i] is not None]
        if col and all(isinstance(v, (dt.date, dt.datetime)) for v in col):
            kinds.append("time")
        elif col and all(_is_number(v) for v in col):
            kinds.append("number")
        else:
            kinds.append("label")
    if kinds.count("number") != 1 or "label" not in kinds:
        return None
    value = kinds.index("number")
    series_i = kinds.index("label")
    axis_i = next(i for i in range(3) if i not in (value, series_i))
    is_time = kinds[axis_i] == "time"
    axis = sorted({r[axis_i] for r in rows if r[axis_i] is not None}, key=lambda v: (str(type(v)), v))
    names = list(dict.fromkeys(r[series_i] for r in rows if r[series_i] is not None))
    if not 2 <= len(axis) <= 60 or not 2 <= len(names) <= 8:
        return None
    cells = {(r[series_i], r[axis_i]): r[value] for r in rows}
    return {"metric": columns[value], "axis_label": columns[axis_i], "series_label": columns[series_i],
            "is_time": is_time, "axis": axis, "names": names, "cells": cells}


def _pivot_chart(p: dict, preferred: str | None) -> tuple[dict, str]:
    note = ""
    kind = preferred if preferred in ("bar", "line") else ("line" if p["is_time"] else "bar")
    if preferred == "pie":
        note = (f"Круговая диаграмма подходит для одной величины без разбивки — показываю "
                f"{VIEW_NAMES[kind]}.")
    chart = {
        "type": kind,
        "title": f"{p['metric']} по «{p['series_label']}»",
        "x_label": p["axis_label"],
        "y_label": p["metric"],
        "categories": [format_value(a) for a in p["axis"]],
        "series": [{"name": str(name),
                    "data": [float(p["cells"][(name, a)]) if p["cells"].get((name, a)) is not None
                             else None for a in p["axis"]]}
                   for name in p["names"]],
    }
    return chart, note


def result_text(columns: list[str], rows: list[tuple], truncated: bool) -> str:
    lines = [" | ".join(columns)]
    lines += [" | ".join(format_value(v) for v in row) for row in rows[:EXPLAIN_ROWS]]
    shown = min(len(rows), EXPLAIN_ROWS)
    if truncated or len(rows) > EXPLAIN_ROWS:
        lines.append(f"(показаны первые {shown} строк, результат неполный)")
    return "\n".join(lines)


def answer_looks_good(answer: str, columns: list[str], rows: list[tuple]) -> bool:
    """Отсеиваем «кашу»: слишком коротко, нет ни одного значения из результата
    или есть неотформатированные длинные числа (1150771281.54)."""
    if len(re.findall(r"[а-яёА-ЯЁ]{3,}", answer)) < 5:
        return False
    if re.search(r"\d{5,}", answer.replace(" ", "\u00a0")):
        return False
    shown = {format_value(v) for row in rows[:EXPLAIN_ROWS] for v in row if v is not None}
    return any(item and item in answer for item in shown)


NON_ADDITIVE = re.compile(r"сред|доля|процент|%|медиан|рейтинг|оценк|минимал|максимал|ставк",
                          re.IGNORECASE)


def is_additive(metric: str) -> bool:
    """Суммы и количества можно складывать в «Всего», средние и доли — нет."""
    return not NON_ADDITIVE.search(metric)


def missing_note(sql: str, table: dict, threshold: float = 0.05) -> str:
    """Оговорка о пропусках в колонках, которые участвуют в расчёте."""
    used = set(re.findall(r'"([^"]+)"', sql))
    notes = []
    for col in table.get("columns") or []:
        quality = (col.get("conversion") or {}).get("quality") or {}
        total = quality.get("total_rows")
        if col["name"] in used and total and quality.get("missing_rows", 0) / total >= threshold:
            notes.append(f"«{col['name']}» заполнено только в "
                         f"{1 - quality['missing_rows'] / total:.0%} строк")
    if not notes:
        return ""
    return "Учтены только заполненные значения: " + "; ".join(notes) + "."


def template_answer(columns: list[str], rows: list[tuple], truncated: bool) -> str:
    """Запасной ответ кодом — скучный, но всегда верный."""
    p = _pivot(columns, rows)
    if p:
        totals = {n: sum(float(p["cells"].get((n, a)) or 0) for a in p["axis"]) for n in p["names"]}
        best = max(totals, key=totals.get)
        peak = max(((n, a) for n in p["names"] for a in p["axis"] if p["cells"].get((n, a)) is not None),
                   key=lambda k: p["cells"][k])
        span = (f" с {format_value(p['axis'][0])} по {format_value(p['axis'][-1])}"
                if p["is_time"] else "")
        text = (f"«{p['metric']}»{span} по {len(p['names'])} значениям «{p['series_label']}». "
                f"Максимум — «{peak[0]}», {format_value(peak[1])}: {format_value(p['cells'][peak])}.")
        if is_additive(p["metric"]):
            text += f" Больше всего в сумме — у «{best}»: {format_value(totals[best])}."
        return text + " Подробности — на графике."
    if len(rows) == 1 and len(columns) == 1:
        return f"{columns[0]}: {format_value(rows[0][0])}."
    if len(rows) == 1:
        return "; ".join(f"{c}: {format_value(v)}" for c, v in zip(columns, rows[0])) + "."
    if len(columns) >= 2 and all(_is_number(r[1]) or r[1] is None for r in rows):
        metric = columns[1]
        if all(isinstance(r[0], (dt.date, dt.datetime)) for r in rows):
            ordered = sorted(rows, key=lambda r: r[0])
            peak = max((r for r in rows if r[1] is not None), key=lambda r: r[1])
            return (f"{metric} за период с {format_value(ordered[0][0])} по "
                    f"{format_value(ordered[-1][0])}: максимум {format_value(peak[1])} "
                    f"({format_value(peak[0])}). Подробности — в таблице и на графике.")
        ranked = sorted((r for r in rows if r[1] is not None), key=lambda r: r[1], reverse=True)
        top = ranked[0]
        text = (f"Наибольшее значение «{metric}» — у «{format_value(top[0])}»: "
                f"{format_value(top[1])}.")
        rest = ranked[1:3]
        if rest:
            text += " Далее: " + ", ".join(f"«{format_value(r[0])}» — {format_value(r[1])}"
                                          for r in rest) + "."
        if is_additive(metric) and not truncated:
            text += f" Всего: {format_value(sum(r[1] for r in ranked))}."
        return text
    more = " (показана часть)" if truncated else ""
    return f"Нашлось строк: {format_int(len(rows))}{more}. Результат — в таблице ниже."


# ---------- память диалога ----------

MAX_SESSIONS = 500
_SESSIONS: "OrderedDict[str, dict]" = OrderedDict()


def _session(session_id: str | None) -> dict:
    """Аналитический контекст диалога: последний вопрос/таблица/SQL и ожидаемое уточнение.
    Хранится в памяти процесса (для хакатона достаточно)."""
    if not session_id:
        return {}
    state = _SESSIONS.pop(session_id, None) or {}
    _SESSIONS[session_id] = state
    while len(_SESSIONS) > MAX_SESSIONS:
        _SESSIONS.popitem(last=False)
    return state


def reset_session(session_id: str) -> None:
    _SESSIONS.pop(session_id, None)


def _match_option(message: str, options: list[str]) -> int | None:
    text = message.strip().lower().strip(".!")
    for i, option in enumerate(options):
        if text == option.lower():
            return i
    if text.isdigit() and 1 <= int(text) <= len(options):
        return int(text) - 1
    hits = [i for i, option in enumerate(options) if overlap(words(text), words(option))]
    return hits[0] if len(hits) == 1 else None


def compare_metric(question: str, table: dict) -> str:
    """Выражение показателя для подсказки сравнения: выручка, упомянутая числовая колонка
    или COUNT(*)."""
    columns = [c["name"] for c in table.get("columns") or []]
    formula = money_formula(columns)
    if formula and MONEY.search(question):
        return formula
    asked = words(question)
    for c in table.get("columns") or []:
        if column_kind(c) in ("integer", "decimal") and overlap(asked, words(c["name"])):
            return f'SUM("{c["name"]}")'
    return "COUNT(*)"


def is_followup(question: str) -> bool:
    """Явное продолжение: «а теперь…», «то же самое…», просьба о виде («по месяцам линией»)."""
    return bool(FOLLOWUP_START.search(question) or FOLLOWUP_WORDS.search(question)
                or requested_view(question))


def is_short_refinement(question: str, tables: list[dict]) -> bool:
    """Короткое уточнение без названия таблицы и без «сколько»: «только Москва», «за 2025 год».
    Окончательно решает выбор таблицы: если вопрос уверенно про другую таблицу — это новый вопрос."""
    asked = words(question)
    if not asked or len(asked) > 3 or Intent.parse(question).is_count:
        return False
    names = set().union(*(words(display_table_name(t["name"])) for t in tables))
    return not overlap(asked, names)


def _values_for_all(runner, tables: list[dict]) -> dict:
    values = {}
    for t in tables:
        try:
            values[t["name"]] = runner.category_values(t)
        except Exception:
            values[t["name"]] = {}
    return values


def _refuse(text: str, suggestions: list[str] | None = None) -> dict:
    suggs = suggestions or DEFAULT_DATASET_SUGGESTIONS
    formatted_answer = text
    if not any(s in text for s in suggs):
        formatted_answer = f"{text}\n\nВы можете запросить, например:\n" + "\n".join(f"- {s}" for s in suggs[:3])
    return {
        "answer": formatted_answer,
        "sql": None,
    }



# ---------- основной сценарий ----------

def answer_question(question: str, catalog: dict, runner, client=None,
                    progress=None, trace: dict | None = None,
                    session_id: str | None = None,
                    table_name: str | None = None,
                    datasource_id: str | None = None, _nested: bool = False) -> dict:
    """Ответ в формате docs/api-contract.md: answer, table, sql, chart, clarification.

    runner — объект с методами run(table, sql) и category_values(table),
    например sql_runner.PostgresRunner.
    trace — необязательный словарь; в него пишутся детали (таблица, SQL, сырые
    строки результата, попытки, ошибки) — для тестов точности и отладки.
    session_id — идентификатор диалога: включает память (уточнения и продолжения).
    table_name — опциональное имя активной таблицы для сужения контекста запроса.
    """
    trace = trace if trace is not None else {}
    trace["errors"] = []
    say = progress or (lambda msg: None)
    catalog = gl.apply(catalog)  # подтверждённые определения из бизнес-словаря (#62)
    if client is None:
        from .client import LLMClient
        client = LLMClient()
    state = _session(session_id)
    if session_id:
        active_ds = str(datasource_id or catalog.get("datasource_id") or catalog.get("schema") or "raw").lower()
        last_ds = state.get("datasource_id")
        if last_ds is not None and last_ds != active_ds:
            state.pop("last", None)
            state.pop("pending", None)
            state.pop("previous", None)
            state.pop("history", None)
        state["datasource_id"] = active_ds
    tables = catalog.get("tables") or []

    if table_name:
        t_clean = table_name.strip()
        matched = []
        for t in tables:
            if match_table_name(t, t_clean):
                matched.append(t)
                break
        if not matched:
            available = [display_table_name(t.get("name", "")) for t in tables]
            available_str = ", ".join(f"«{n}»" for n in available if n)
            return {
                "answer": f"Таблица «{table_name}» не найдена в каталоге данных. Доступные таблицы: {available_str}.",
                "chart": None,
                "table": None,
                "sql": None,
                "clarification": None,
            }
        tables = matched
        catalog = {**catalog, "tables": tables}

    by_name = {t["name"]: t for t in tables}
    question = question.strip()

    # 0. Ответ на наше уточнение?
    forced_table = None
    resolved_unit = False
    pending = state.pop("pending", None)
    if pending:
        choice = _match_option(question, pending["options"])
        if choice is not None:
            say(f"Уточнение: {pending['options'][choice]}")
            if pending["kind"] == "table":
                forced_table = by_name.get(pending["tables"][choice])
                question = pending["question"]
            elif pending["kind"] == "unit":
                forced_table = by_name.get(pending["table"])
                question = f"{pending['question']} ({pending['units'][choice]})"
                resolved_unit = True

    # Проверка деструктивных действий (DDL/DML)
    mutation_refusal = check_destructive_mutation(question)
    if mutation_refusal:
        return mutation_refusal.to_dict()

    # Проверка запроса отсутствующих в датасете сущностей и метрик (NPS, ROI, LTV, ТВ-реклама и т.д.)
    # (кроме терминов, которые команда сама определила в бизнес-словаре: «CSAT» → оценка клиента)
    out_of_scope_refusal = detect_out_of_scope(question)
    if out_of_scope_refusal:
        return out_of_scope_refusal.to_dict()

    # Неоднозначный / слишком обобщённый вопрос (только если таблица не зафиксирована)
    if not (table_name or len(tables) == 1):
        ambiguous_refusal = detect_ambiguous_query(question)
        if ambiguous_refusal:
            trace["clarification"] = "ambiguous"
            return ambiguous_refusal.to_dict()


    # Сценарии «что если» (#56) и прогноз (сценарий 10): база — обычным путём, расчёт — кодом
    if not _nested and (wi.is_whatif(question) or wi.is_forecast(question)):
        scenario = _scenario_answer(question, catalog, runner, client, say, trace, state,
                                    session_id=session_id, datasource_id=datasource_id,
                                    forced=forced_table)
        if scenario is not None:
            return scenario

    # 1. Только сменить вид прошлого результата («покажи кругом») — без модели
    last = state.get("last")
    if is_pure_view_request(question):
        if not last or last["table"] not in by_name:
            return _refuse("Сначала задайте вопрос к данным — потом я покажу результат "
                           "в нужном виде.")
        view = requested_view(question)
        say(f"Меняю вид прошлого результата: {view}")
        result = runner.run(by_name[last["table"]], last["sql"])
        trace.update(table=last["table"], sql=last["sql"], columns=result.columns, rows=result.rows,
                     view=view)
        response = _result_response(result, last["sql"], by_name[last["table"]], view,
                                    last["question"])
        response["answer"] = (f"Вот тот же результат {VIEW_NAMES[view]}." + (
            " " + response.pop("_note") if response.get("_note") else ""))
        response.pop("_note", None)
        return response

    # 2. Продолжение прошлого вопроса?
    previous = None
    values_by_table = None
    if not forced_table and last and last["table"] in by_name:
        followup = is_followup(question)
        if not followup and is_short_refinement(question, tables):
            values_by_table = _values_for_all(runner, tables)
            guess, _ = choose_table(question, tables, values_by_table)
            # только если вопрос указывает на ту же таблицу: «Какая погода в Москве?» после
            # вопроса о выручке — не уточнение, а вопрос не по данным
            followup = guess is not None and guess["name"] == last["table"]
        if followup:
            previous = last
            forced_table = by_name[last["table"]]
            say(f"Продолжение вопроса: «{last['question']}»")

    # 3. Таблица
    if forced_table is not None:
        table = forced_table
    else:
        say("Выбираю таблицу")
        values_by_table = values_by_table or _values_for_all(runner, tables)
        table, candidates = choose_table(question, tables, values_by_table)
        if table is None:
            if not candidates:
                return _refuse("Этот вопрос не похож на вопрос к загруженным данным. "
                               "Я могу посчитать показатели по таблицам: "
                               + ", ".join(f"«{display_table_name(t['name'])}»" for t in tables)
                               + ". Например: «Какая выручка по каналам продаж?»")
            options = [display_table_name(t["name"]) for t in candidates]
            state["pending"] = {"kind": "table", "question": question, "options": options,
                                "tables": [t["name"] for t in candidates]}
            trace["clarification"] = "table"
            bullet_opts = "\n".join(f"- {opt}" for opt in options[:3])
            return {
                "answer": (
                    "Подходит несколько таблиц. Уточните, пожалуйста, по каким данным считать:\n"
                    f"{bullet_opts}"
                ),
                "chart": None,
                "table": None,
                "sql": None,
                "clarification": {"question": "По каким данным считать?", "options": options},
            }

    table_name = display_table_name(table["name"])
    trace["table"] = table["name"]
    say(f"Таблица: {table_name}")

    full_question = f"{previous['question']} {question}" if previous else question
    intent = Intent.parse(full_question)

    # 3. Единица счёта неоднозначна?
    if not previous and not resolved_unit:
        units = unit_options(question, intent, table)
        if units:
            options = [label for label, _ in units]
            state["pending"] = {"kind": "unit", "question": question, "table": table["name"],
                                "options": options, "units": [u for _, u in units]}
            trace["clarification"] = "unit"
            bullet_units = "\n".join(f"- {opt}" for opt in options)
            return {
                "answer": (
                    "Уточните, пожалуйста, что именно посчитать: в этой таблице одна "
                    f"строка — это позиция, а не целая операция:\n{bullet_units}"
                ),
                "chart": None,
                "table": None,
                "sql": None,
                "clarification": {"question": "Что считать?", "options": options},
            }


    # 4. Сравнение периодов? Границы дат считает код.
    periods: list = []
    date_col = None
    extra_hint = ""
    if cmp.is_compare(full_question):
        kinds = {c["name"]: column_kind(c) for c in table.get("columns") or []}
        date_col = cmp.choose_date_column(table, full_question, kinds)
        latest = None
        if date_col and hasattr(runner, "date_range"):
            try:
                latest = runner.date_range(table, date_col)[1]
            except Exception:
                latest = None
        if isinstance(latest, dt.datetime):
            latest = latest.date()
        periods = cmp.parse_periods(full_question, latest) if date_col else []
        if len(periods) >= 2:
            periods = periods[-2:]
            extra_hint = cmp.compare_hint(periods, date_col, compare_metric(full_question, table))
            trace["periods"] = [p.label for p in periods]
            say("Сравнение: " + " и ".join(p.label for p in periods))
        else:
            periods = []

    if periods and previous:
        # сравнение перестраивает запрос целиком: прошлый SQL только сбивает модель
        question, previous = full_question, None

    if adv.is_seasonality(full_question):
        kinds_map = {c["name"]: column_kind(c) for c in table.get("columns") or []}
        date_col_season = cmp.choose_date_column(table, full_question, kinds_map)
        if date_col_season:
            extra_hint += adv.seasonality_hint(date_col_season, compare_metric(full_question, table))


    formula = money_formula([c["name"] for c in table.get("columns") or []])
    if formula and MONEY.search(full_question):
        extra_hint += f"\nВыручка в этой таблице: {formula}.\n"

    # Утверждённый бизнес-KPI (#57): формула из data/kpi-definitions.json, а не своя.
    # В сравнении периодов не используем: FILTER к составной формуле так не приклеить.
    kpi = None if periods else kp.for_question(full_question, table, catalog.get("schema"))
    if kpi:
        extra_hint += kp.hint(kpi)
        trace["kpi"] = kpi.id
        say(f"Утверждённый KPI: {kpi.title}")

    # 5. SQL с проверками
    values = (values_by_table or {}).get(table["name"])
    if values is None:
        values = runner.category_values(table)
    column_names = [c["name"] for c in table.get("columns") or []]
    numeric_columns = [c["name"] for c in table.get("columns") or []
                       if column_kind(c) in ("integer", "decimal")]
    table_words = words(display_table_name(table["name"]))
    history: list[dict] = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        trace["attempts"] = attempt
        say(f"Пишу SQL (попытка {attempt})")
        reply = client_chat(client, build_sql_messages(question, table, values, history, previous,
                                                       extra_hint))
        try:
            sql = adjust_sql(validate_sql(extract_sql(reply), column_names), intent)
            sql, removed = strip_unmentioned_filters(sql, full_question, values, table_words)
            if removed:
                say(f"Убраны лишние фильтры: {'; '.join(removed)}")
                trace.setdefault("removed_filters", []).extend(removed)
            problems = check_sql(sql, full_question, intent, values,
                                 strict=attempt < MAX_ATTEMPTS, ignore=table_words,
                                 numeric_columns=numeric_columns, columns=column_names,
                                 aliases=gl.aliases(table))
            if periods:
                problems += cmp.check_compare_sql(sql, periods, date_col)
            if kpi and attempt < MAX_ATTEMPTS:
                problems += kp.check(sql, kpi)
            if problems:
                raise SQLRejected(" ".join(problems))
            result = runner.run(table, sql)
            break
        except SQLRejected as exc:
            last_error = str(exc)
        except Exception as exc:  # ошибка базы: текст возвращаем модели
            last_error = f"Ошибка PostgreSQL: {str(exc).splitlines()[0]}"
        trace["errors"].append(last_error)
        say(f"Ошибка: {last_error}")
        history += [{"role": "assistant", "content": reply},
                    {"role": "user", "content": f"{last_error}\nИсправь запрос. "
                                                "Ответь только SQL в блоке ```sql```."}]
    else:
        return _refuse("Не получилось надёжно посчитать ответ на этот вопрос, поэтому цифр не "
                       "показываю. Попробуйте сформулировать проще: какой показатель и в каком "
                       "разрезе нужен.")

    trace.update(sql=sql, columns=result.columns, rows=result.rows)
    state["last"] = {"question": full_question, "table": table["name"], "sql": sql}
    shown_sql = f"-- t = данные таблицы «{table_name}» с приведёнными типами\n{sql}"
    if not result.rows or all(v is None for row in result.rows for v in row):
        return {"answer": "По такому запросу в данных ничего не нашлось. Проверьте условия "
                          "(период, значения фильтров).", "sql": shown_sql}

    if periods:
        enriched = cmp.enrich_compare(result.columns, result.rows, periods)
        if enriched:
            columns, rows, pair = enriched
            answer = cmp.compare_summary(columns, rows, pair, format_value)
            note = missing_note(sql, table)
            response = {
                "answer": f"{answer} {note}".strip(),
                "table": {"columns": columns,
                          "rows": [[_format_change(c, v) for c, v in zip(columns, row)] for row in rows]},
                "sql": shown_sql,
                "chart": cmp.compare_chart(columns, rows, pair, format_value),
                "table_name": table["name"],
            }
            return response

    if _nested:
        # промежуточный расчёт (база сценария или прогноза): текст не показывается — модель не зовём
        response = _result_response(result, sql, table, None, full_question)
        response.pop("_note", None)
        response["answer"] = template_answer(result.columns, result.rows, result.truncated)
        return response

    say("Объясняю результат")
    data = result_text(result.columns, result.rows, result.truncated)
    answer = client_chat(client, [
        {"role": "system", "content": EXPLAIN_PROMPT},
        {"role": "user", "content": f"Вопрос: {full_question}\n\nТаблица «{table_name}», "
                                    f"результат запроса:\n{data}"},
    ], max_tokens=400).strip()
    answer = re.sub(r"^(прямой\s+)?ответ\s*:\s*", "", answer, flags=re.IGNORECASE)
    if not answer_looks_good(answer, result.columns, result.rows):
        say(f"Ответ модели неудачный — использую шаблон. Модель написала: {answer[:150]!r}")
        answer = template_answer(result.columns, result.rows, result.truncated)
    note = missing_note(sql, table)
    if note:
        answer = f"{answer} {note}"
    if kpi and kp.check(sql, kpi) == []:
        answer = f"{answer} {kp.note(kpi)}"

    if cmp.is_compare(full_question):
        answer = f"{answer} {two_segment_difference(result.columns, result.rows)}".strip()

    if adv.is_seasonality(full_question):
        season_analysis = adv.analyze_seasonality(result.columns, result.rows)
        if season_analysis:
            season_text = adv.seasonality_summary(season_analysis)
            answer = f"{answer} {season_text}".strip()

    if adv.is_correlation(full_question) and len(result.columns) >= 2 and len(result.rows) >= 3:
        try:
            x_vals = [float(str(r[0]).replace(",", ".").replace(" ", "")) for r in result.rows if r[0] is not None]
            y_vals = [float(str(r[1]).replace(",", ".").replace(" ", "")) for r in result.rows if r[1] is not None]
            if len(x_vals) == len(y_vals) and len(x_vals) >= 3:
                r_val = adv.calculate_pearson(x_vals, y_vals)
                if r_val is not None:
                    corr_text = adv.correlation_summary(r_val, result.columns[0], result.columns[1])
                    answer = f"{answer} {corr_text}".strip()
        except Exception:
            pass

    if adv.is_segmentation(full_question) and len(result.columns) >= 2 and len(result.rows) >= 2:
        seg_text = adv.segmentation_summary(result.columns, result.rows)
        if seg_text:
            answer = f"{answer}\n\n{seg_text}".strip()

    response = _result_response(result, sql, table, requested_view(question), full_question)
    note = response.pop("_note", "")
    response["answer"] = f"{answer} {note}".strip()
    return response


def _scenario_answer(question: str, catalog: dict, runner, client, say, trace: dict, state: dict,
                     session_id: str | None = None, datasource_id: str | None = None,
                     forced: dict | None = None) -> dict | None:
    """«Что будет, если …» и «прогноз на …». None — обычный путь (не удалось разобрать)."""
    tables = catalog.get("tables") or []
    last = state.get("last")
    forecast = wi.is_forecast(question)
    base_text, _ = wi.split_question(question)

    # таблица: продолжение прошлого вопроса («а если скидка вырастет?») или выбор по вопросу
    table = forced  # таблица, выбранная пользователем в ответ на уточнение
    if table is None and last and not words(base_text) and not forecast:
        table = next((t for t in tables if t["name"] == last["table"]), None)
    if table is None:
        table, _ = choose_table(question, tables, _values_for_all(runner, tables))
    if table is None:
        return None
    columns = [c["name"] for c in table.get("columns") or []]
    one_table = {**catalog, "tables": [table]}

    if forecast:
        base_q = wi.forecast_base_question(question)
    elif last and not words(base_text) and last["table"] == table["name"]:
        base_q = last["question"]
    else:
        base_q = wi.base_question(question, columns)
    say(f"Сценарий: сначала считаю базу — «{base_q}»")
    base_trace: dict = {}
    base = answer_question(base_q, one_table, runner, client=client, progress=say, trace=base_trace,
                           datasource_id=datasource_id, _nested=True)
    trace.update(table=table["name"], base_question=base_q, errors=base_trace.get("errors", []),
                 attempts=base_trace.get("attempts"))
    if "rows" not in base_trace:
        return base  # уточнение или отказ базового вопроса — показываем как есть
    sql, cols, rows = base_trace["sql"], base_trace["columns"], base_trace["rows"]
    table_name = display_table_name(table["name"])

    if forecast:
        return _forecast_response(question, sql, cols, rows, table_name, trace)

    change = wi.parse_change(question)
    _, condition = wi.split_question(question)
    numeric = [c["name"] for c in table.get("columns") or [] if column_kind(c) in ("integer", "decimal")]
    driver = wi.driver_column(condition, numeric)
    new_sql = wi.scenario_sql(sql, driver, change) if driver else None
    new_rows = None
    if new_sql:
        say("Пересчитываю с изменением")
        try:
            new_rows = runner.run(table, new_sql).rows
        except Exception as exc:
            trace["errors"].append(f"Сценарий: {str(exc).splitlines()[0]}")
            new_sql = None
    if new_sql is None and change.kind == "pp":
        return _refuse(f"Не получилось применить изменение на {abs(change.value):g} п.п.: величина "
                       "не входит в расчёт показателя. Уточните, какую колонку менять.")
    combined = wi.combine(cols, rows, new_rows, change)
    if not combined:
        return base
    out_cols, out_rows, metric = combined
    trace.update(sql=new_sql or sql, columns=out_cols, rows=out_rows, scenario=change.text,
                 driver=driver, proportional=new_sql is None)
    answer = wi.whatif_summary(metric, out_cols, out_rows, driver, change, new_sql is None, format_value)
    shown = f"-- t = данные таблицы «{table_name}» с приведёнными типами\n-- база:\n{sql}"
    if new_sql:
        shown += f"\n-- сценарий ({change.text}):\n{new_sql}"
    if session_id:
        state["last"] = {"question": base_q, "table": table["name"], "sql": sql}
    return {
        "answer": answer,
        "table": {"columns": out_cols,
                  "rows": [[_format_change(c, v) for c, v in zip(out_cols, r)] for r in out_rows]},
        "sql": shown,
        "chart": wi.whatif_chart(out_cols, out_rows, format_value),
    }


def _forecast_response(question: str, sql: str, cols: list[str], rows: list[tuple],
                       table_name: str, trace: dict) -> dict:
    """Помесячный ряд + линейный тренд (advanced_analytics.linear_extrapolation)."""
    series = [(r[0], float(r[-1])) for r in rows
              if isinstance(r[0], (dt.date, dt.datetime)) and _is_number(r[-1])]
    series.sort(key=lambda x: x[0])
    if len(series) < 6:
        return _refuse("Для прогноза нужен помесячный ряд хотя бы из 6 точек — "
                       "в данных его не получилось построить.")
    steps = wi.forecast_steps(question)
    projected = adv.linear_extrapolation([v for _, v in series], steps)
    months = []
    d = series[-1][0]
    for _ in range(steps):
        d = wi.next_month(d)
        months.append(d)
    metric = cols[-1]
    last_label, last_value = format_value(series[-1][0]), series[-1][1]
    change = (projected[-1] - last_value) / abs(last_value) * 100 if last_value else 0.0
    trend = "рост" if projected[-1] > last_value else "снижение"
    change_txt = f"на {abs(change):.1f}%".replace(".", ",")
    money = lambda v: format_value(Decimal(str(round(v, 2))))  # noqa: E731
    answer = (f"Прогноз «{metric}» на {steps} мес. по линейному тренду за {len(series)} мес.: "
              f"{', '.join(money(v) for v in projected)}. "
              f"К {format_value(months[-1])} — {trend} {change_txt} к последнему факту "
              f"({last_label}: {money(last_value)}). "
              "Тренд не учитывает сезонность и внешние события.")
    categories = [format_value(m) for m, _ in series] + [format_value(m) for m in months]
    fact = [v for _, v in series] + [None] * steps
    forecast = [None] * (len(series) - 1) + [series[-1][1]] + [round(v, 2) for v in projected]
    trace.update(rows=[(m, v) for m, v in series], forecast=projected)
    return {
        "answer": answer,
        "table": {"columns": [cols[0], f"{metric} (прогноз)"],
                  "rows": [[format_value(m), format_value(Decimal(str(round(v, 2))))]
                           for m, v in zip(months, projected)]},
        "sql": f"-- t = данные таблицы «{table_name}» с приведёнными типами\n-- фактический ряд:\n{sql}",
        "chart": {"type": "line", "title": f"{metric}: факт и прогноз", "x_label": cols[0],
                  "y_label": metric, "categories": categories,
                  "series": [{"name": "Факт", "data": fact}, {"name": "Прогноз", "data": forecast}]},
    }


def _format_change(column: str, value) -> str:
    if column == "Изменение, %" and value is not None:
        return f"{'+' if value > 0 else ''}{value:.1f}%".replace(".", ",")
    if column == "Изменение" and value is not None and value > 0:
        return "+" + format_value(value)
    return format_value(value)


def two_segment_difference(columns: list[str], rows: list[tuple]) -> str:
    """«Сравни Москву и Казань»: два значения — разница кодом, а не моделью."""
    if len(rows) != 2 or len(columns) != 2 or not all(_is_number(r[1]) for r in rows):
        return ""
    (a, x), (b, y) = sorted(rows, key=lambda r: r[1], reverse=True)
    diff = float(x) - float(y)
    pct = f" ({diff / float(y) * 100:.1f}%)".replace(".", ",") if y else ""
    return (f"«{format_value(a)}» больше «{format_value(b)}» на {format_value(diff)}{pct} "
            f"по показателю «{columns[1]}».")


def _result_response(result, sql: str, table: dict, view: str | None, question: str) -> dict:
    """Таблица, SQL и график в формате api-contract; _note — пояснение про вид графика."""
    table_name = display_table_name(table["name"])
    response = {
        "table": {"columns": result.columns,
                  "rows": [[format_value(v) for v in row] for row in result.rows]},
        "sql": f"-- t = данные таблицы «{table_name}» с приведёнными типами\n{sql}",
        "table_name": table["name"],
    }
    chart, note = suggest_chart(result.columns, result.rows, view, question)
    if chart:
        response["chart"] = chart
    response["_note"] = note
    return response


def client_chat(client, messages: list[dict], max_tokens: int = 600) -> str:
    return client.chat(messages, max_tokens=max_tokens, temperature=0)


def _print_result(result: dict) -> None:
    print()
    print(result["answer"])
    if result.get("clarification"):
        for i, option in enumerate(result["clarification"]["options"], 1):
            print(f"  {i}. {option}")
    if result.get("sql"):
        print("\nКак посчитано:\n" + result["sql"])
    if result.get("table"):
        print()
        print(" | ".join(result["table"]["columns"]))
        for row in result["table"]["rows"][:15]:
            print(" | ".join(row))
    if result.get("chart"):
        print(f"\nГрафик: {result['chart']['type']}, точек: {len(result['chart']['categories'])}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Ответ на вопрос по данным")
    parser.add_argument("question", nargs="?", help="вопрос; без него — режим диалога")
    parser.add_argument("--catalog", type=Path, default=Path("data/schema-catalog.json"))
    args = parser.parse_args()
    try:
        catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"Не удалось прочитать каталог: {exc}", file=sys.stderr)
        return 1

    from .client import LLMClient
    from .sql_runner import PostgresRunner
    runner = PostgresRunner.from_env()
    client = LLMClient()
    progress = lambda msg: print(f"... {msg}", file=sys.stderr)  # noqa: E731

    if args.question:
        _print_result(answer_question(args.question, catalog, runner, client, progress))
        return 0

    print("Режим диалога: пишите вопросы, пустая строка — выход.")
    while True:
        try:
            question = input("\n> ").strip()
        except EOFError:
            break
        if not question:
            break
        _print_result(answer_question(question, catalog, runner, client, progress,
                                      session_id="cli"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
