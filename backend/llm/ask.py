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

from .catalog_summary import TYPE_LABELS, display_table_name, format_int
from .semantics import (Intent, adjust_sql, check_sql, counted_noun, overlap, same_word,
                        strip_unmentioned_filters, strong_word, words)
from .sql_guard import SQLRejected, extract_sql, validate_sql
from .sql_runner import column_kind

MAX_ATTEMPTS = 3
EXPLAIN_ROWS = 20

WRITE_INTENT = re.compile(
    r"\b(удал\w*|измен\w*|обнови\w*|перезапиш\w*|очист\w*|вставь|добавь\s+(строк|запис)\w*|"
    r"drop|delete|truncate|insert|update|alter)\b", re.IGNORECASE)
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
    return line


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
                       previous: dict | None = None) -> list[dict]:
    """previous — прошлый вопрос и SQL в этом диалоге, если новый вопрос его уточняет."""
    columns = "\n".join(_column_line(c, values) for c in table.get("columns") or [])
    example_q, example_sql = _example(table)
    mentioned = mentioned_columns(question, table)
    hint = ("\nПодсказка: в вопросе, вероятно, речь о колонках "
            + ", ".join(f'"{c}"' for c in mentioned) + ".\n") if mentioned else ""
    system = SQL_PROMPT.format(columns=columns, example_q=example_q,
                               example_sql=example_sql, hint=hint)
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
        hits = overlap(asked, words(col["name"]))
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
    scores = []
    for t in tables:
        name_words = words(display_table_name(t["name"]))
        col_words = set().union(*(words(c["name"]) for c in t.get("columns") or []))
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
        scores.append(score)
        if detail is not None:
            detail.append(structural)
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
    analytic = bool(ANALYTIC_WORDS.search(question))
    # «Какая погода в Москве?» — совпало только значение «Москва», а вопрос не аналитический
    if best == 0 or (not analytic and max(structural) == 0):
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
        if number.is_integer() and abs(number) >= 100:
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


def suggest_chart(columns: list[str], rows: list[tuple]) -> dict | None:
    """Простой график: первая колонка — подписи, остальные числовые — серии."""
    if len(columns) < 2 or not 2 <= len(rows) <= 50:
        return None
    numeric = [i for i in range(1, len(columns))
               if all(_is_number(r[i]) or r[i] is None for r in rows)]
    if not numeric or len(numeric) != len(columns) - 1:
        return None
    is_time = all(isinstance(r[0], (dt.date, dt.datetime)) for r in rows)
    if is_time:
        rows = sorted(rows, key=lambda r: r[0])
    return {
        "type": "line" if is_time else "bar",
        "title": columns[numeric[0]] if len(numeric) == 1 else "",
        "x_label": columns[0],
        "y_label": columns[numeric[0]] if len(numeric) == 1 else "",
        "categories": [format_value(r[0]) for r in rows],
        "series": [{"name": columns[i],
                    "data": [float(r[i]) if r[i] is not None else None for r in rows]}
                   for i in numeric],
    }


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


def is_followup(question: str) -> bool:
    return bool(FOLLOWUP_START.search(question) or FOLLOWUP_WORDS.search(question))


def _values_for_all(runner, tables: list[dict]) -> dict:
    values = {}
    for t in tables:
        try:
            values[t["name"]] = runner.category_values(t)
        except Exception:
            values[t["name"]] = {}
    return values


def _refuse(text: str) -> dict:
    return {"answer": text, "sql": None}


# ---------- основной сценарий ----------

def answer_question(question: str, catalog: dict, runner, client=None,
                    progress=None, trace: dict | None = None,
                    session_id: str | None = None) -> dict:
    """Ответ в формате docs/api-contract.md: answer, table, sql, chart, clarification.

    runner — объект с методами run(table, sql) и category_values(table),
    например sql_runner.PostgresRunner.
    trace — необязательный словарь; в него пишутся детали (таблица, SQL, сырые
    строки результата, попытки, ошибки) — для тестов точности и отладки.
    session_id — идентификатор диалога: включает память (уточнения и продолжения).
    """
    trace = trace if trace is not None else {}
    trace["errors"] = []
    say = progress or (lambda msg: None)
    if client is None:
        from .client import LLMClient
        client = LLMClient()
    state = _session(session_id)
    tables = catalog.get("tables") or []
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

    if WRITE_INTENT.search(question):
        return _refuse("Я работаю с данными только на чтение: могу считать и показывать, "
                       "но не изменять и не удалять данные.")

    # 1. Продолжение прошлого вопроса?
    previous = None
    last = state.get("last")
    if not forced_table and last and is_followup(question) and last["table"] in by_name:
        previous = last
        forced_table = by_name[last["table"]]
        say(f"Продолжение вопроса: «{last['question']}»")

    # 2. Таблица
    values_by_table = None
    if forced_table is not None:
        table = forced_table
    else:
        say("Выбираю таблицу")
        values_by_table = _values_for_all(runner, tables)
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
            return {"answer": "Подходит несколько таблиц. Уточните, пожалуйста, по каким данным считать.",
                    "clarification": {"question": "По каким данным считать?", "options": options}}

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
            return {"answer": "Уточните, пожалуйста, что именно посчитать: в этой таблице одна "
                              "строка — это позиция, а не целая операция.",
                    "clarification": {"question": "Что считать?", "options": options}}

    # 4. SQL с проверками
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
        reply = client_chat(client, build_sql_messages(question, table, values, history, previous))
        try:
            sql = adjust_sql(validate_sql(extract_sql(reply), column_names), intent)
            sql, removed = strip_unmentioned_filters(sql, full_question, values, table_words)
            if removed:
                say(f"Убраны лишние фильтры: {'; '.join(removed)}")
                trace.setdefault("removed_filters", []).extend(removed)
            problems = check_sql(sql, full_question, intent, values,
                                 strict=attempt < MAX_ATTEMPTS, ignore=table_words,
                                 numeric_columns=numeric_columns, columns=column_names)
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

    response = {
        "answer": answer,
        "table": {"columns": result.columns,
                  "rows": [[format_value(v) for v in row] for row in result.rows]},
        "sql": shown_sql,
    }
    chart = suggest_chart(result.columns, result.rows)
    if chart:
        response["chart"] = chart
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
