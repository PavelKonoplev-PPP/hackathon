"""Сценарий 2 (issue #4): ответ на вопрос по данным.

Шаги (каждый — короткий запрос, чтобы справлялась и маленькая модель):
1. Выбор таблицы: модель видит названия таблиц и колонок, отвечает номером.
2. SQL: модель видит только колонки выбранной таблицы `t` с типами и
   значениями категорий; пишет один SELECT.
3. Проверка (sql_guard) и выполнение (sql_runner). При ошибке модель получает
   её текст и исправляет запрос (до 3 попыток).
4. Объяснение: модель пересказывает результат (числа уже отформатированы кодом).

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

from .catalog_summary import TYPE_LABELS, display_table_name, format_int
from .sql_guard import SQLRejected, extract_sql, validate_sql
from .sql_runner import column_kind

MAX_ATTEMPTS = 3
EXPLAIN_ROWS = 20

PICK_PROMPT = """Ты помогаешь найти данные для ответа на вопрос.
Ниже пронумерованный список таблиц с их колонками.
Ответь ТОЛЬКО номером таблицы (одно число), по которой лучше всего ответить на вопрос."""

STOP_WORDS = {"какой", "какая", "какие", "каких", "какое", "сколько", "больше", "меньше",
              "всего", "самый", "самая", "самые", "каждый", "каждого", "который", "среди",
              "между", "через", "покажи", "показать", "посчитай", "данные", "данных",
              "таблица", "таблицы", "приносит", "лучше", "хуже", "чаще", "среднее"}

SQL_PROMPT = """Ты пишешь SQL для PostgreSQL 16. Все данные — в одной таблице t.

Колонки таблицы t (названия пиши ТОЧНО так и ВСЕГДА в двойных кавычках):
{columns}

Правила:
- Ровно один запрос SELECT ... FROM t. Никаких других таблиц и команд.
- Числа и даты в t уже нужного типа: не преобразуй их и не меняй запятые.
- Месяц: date_trunc('month', "колонка с датой"); год: date_trunc('year', ...).
- Давай результатам понятные русские имена: AS "Выручка, руб.".
- Сумма денег (выручка, оборот) = количество × цена за единицу; если есть скидка
  в процентах — умножай ещё на (1 - скидка / 100.0).
- Для «какой самый», «больше всего», «топ» — сгруппируй по нужной колонке и выведи
  все группы по убыванию: ORDER BY ... DESC LIMIT 10.
- Для фильтра по категории используй только значения из списка.
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
                    f'GROUP BY "{name}"\nORDER BY "Количество" DESC\nLIMIT 10')
    return "Сколько всего записей?", 'SELECT COUNT(*) AS "Количество" FROM t'


def build_sql_messages(question: str, table: dict, values: dict[str, list[str]],
                       history: list[dict] | None = None) -> list[dict]:
    columns = "\n".join(_column_line(c, values) for c in table.get("columns") or [])
    example_q, example_sql = _example(table)
    mentioned = mentioned_columns(question, table)
    hint = ("\nПодсказка: в вопросе, вероятно, речь о колонках "
            + ", ".join(f'"{c}"' for c in mentioned) + ".\n") if mentioned else ""
    system = SQL_PROMPT.format(columns=columns, example_q=example_q,
                               example_sql=example_sql, hint=hint)
    return [{"role": "system", "content": system},
            {"role": "user", "content": f"Вопрос: {question}"},
            *(history or [])]


def _stems(text: str) -> set[str]:
    """Грубые основы слов: первые 5 букв (продаж/продажи/продажам -> продa...)."""
    words = re.findall(r"[а-яёa-z]+", text.lower().replace("_", " "))
    return {w[:5] for w in words if len(w) >= 4 and w not in STOP_WORDS}


def mentioned_columns(question: str, table: dict) -> list[str]:
    """Колонки, слова из названий которых есть в вопросе (подсказка для модели)."""
    asked = _stems(question)
    return [c["name"] for c in table.get("columns") or [] if asked & _stems(c["name"])]


def keyword_scores(question: str, tables: list[dict]) -> list[int]:
    """Сколько основ слов вопроса встречается в названии таблицы и её колонках."""
    asked = _stems(question)
    scores = []
    for t in tables:
        name_stems = _stems(display_table_name(t["name"]))
        col_stems = set().union(*(_stems(c["name"]) for c in t.get("columns") or []))
        scores.append(2 * len(asked & name_stems) + len(asked & col_stems))
    return scores


def pick_table(client, question: str, catalog: dict, say=None) -> dict | None:
    """Модель выбирает таблицу; совпадение слов страхует от её ошибок."""
    tables = catalog.get("tables") or []
    if not tables:
        return None
    listing = "\n".join(
        f"{i}. {display_table_name(t['name'])}: "
        + ", ".join(c["name"] for c in t.get("columns") or [])
        for i, t in enumerate(tables, 1)
    )
    reply = client.chat([
        {"role": "system", "content": PICK_PROMPT},
        {"role": "user", "content": f"Таблицы:\n{listing}\n\nВопрос: {question}\nНомер таблицы:"},
    ], max_tokens=10, temperature=0)
    if say:
        say(f"Модель выбрала: {reply.strip()[:40]!r}")

    scores = keyword_scores(question, tables)
    best = max(range(len(tables)), key=lambda i: scores[i])

    choice = None
    match = re.search(r"\d+", reply)
    if match and 1 <= int(match.group()) <= len(tables):
        choice = int(match.group()) - 1
    else:
        for i, t in enumerate(tables):  # модель назвала таблицу словами
            if display_table_name(t["name"]).lower() in reply.lower():
                choice = i
                break

    if choice is not None and not (scores[choice] == 0 and scores[best] >= 2):
        return tables[choice]
    if scores[best] > 0:  # модель не ответила или явно промахнулась
        return tables[best]
    return None


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


def widen_top1(sql: str) -> str:
    """«Какой самый...» модель часто решает через LIMIT 1 — для сравнения
    полезнее видеть все группы (и строить график)."""
    if re.search(r"\bgroup\s+by\b", sql, re.IGNORECASE):
        return re.sub(r"\blimit\s+1\s*$", "LIMIT 10", sql, flags=re.IGNORECASE)
    return sql


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


# ---------- основной сценарий ----------

def answer_question(question: str, catalog: dict, runner, client=None,
                    progress=None) -> dict:
    """Ответ в формате docs/api-contract.md: answer, table, sql, chart, clarification.

    runner — объект с методами run(table, sql) и category_values(table),
    например sql_runner.PostgresRunner.
    """
    if client is None:
        from .client import LLMClient
        client = LLMClient()
    say = progress or (lambda msg: None)

    say("Выбираю таблицу")
    table = pick_table(client, question, catalog, say)
    if table is None:
        names = [display_table_name(t["name"]) for t in catalog.get("tables") or []]
        return {
            "answer": "Не нашёл в данных, по какой таблице ответить на этот вопрос. "
                      "Уточните, пожалуйста, о каких данных речь.",
            "clarification": {"question": "О каких данных вопрос?", "options": names[:6]},
        }
    table_name = display_table_name(table["name"])
    say(f"Таблица: {table_name}")

    values = runner.category_values(table)
    column_names = [c["name"] for c in table.get("columns") or []]
    history: list[dict] = []
    last_error = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        say(f"Пишу SQL (попытка {attempt})")
        reply = client.chat(build_sql_messages(question, table, values, history),
                            max_tokens=600, temperature=0)
        try:
            sql = widen_top1(validate_sql(extract_sql(reply), column_names))
            result = runner.run(table, sql)
            break
        except SQLRejected as exc:
            last_error = str(exc)
        except Exception as exc:  # ошибка базы: текст возвращаем модели
            last_error = f"Ошибка PostgreSQL: {str(exc).splitlines()[0]}"
        say(f"Ошибка: {last_error}")
        history += [{"role": "assistant", "content": reply},
                    {"role": "user", "content": f"{last_error}\nИсправь запрос. "
                                                "Ответь только SQL в блоке ```sql```."}]
    else:
        return {"answer": "Не получилось построить запрос к данным для этого вопроса. "
                          "Попробуйте переформулировать его проще или конкретнее.",
                "sql": None}

    shown_sql = f"-- t = данные таблицы «{table_name}» с приведёнными типами\n{sql}"
    if not result.rows:
        return {"answer": "По такому запросу в данных ничего не нашлось.", "sql": shown_sql}

    say("Объясняю результат")
    data = result_text(result.columns, result.rows, result.truncated)
    answer = client.chat([
        {"role": "system", "content": EXPLAIN_PROMPT},
        {"role": "user", "content": f"Вопрос: {question}\n\nТаблица «{table_name}», "
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Ответ на вопрос по данным")
    parser.add_argument("question")
    parser.add_argument("--catalog", type=Path, default=Path("data/schema-catalog.json"))
    args = parser.parse_args()
    try:
        catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"Не удалось прочитать каталог: {exc}", file=sys.stderr)
        return 1

    from .sql_runner import PostgresRunner
    runner = PostgresRunner.from_env()
    result = answer_question(args.question, catalog, runner,
                             progress=lambda msg: print(f"... {msg}", file=sys.stderr))
    print()
    print(result["answer"])
    if result.get("sql"):
        print("\nКак посчитано:\n" + result["sql"])
    if result.get("table"):
        print()
        print(" | ".join(result["table"]["columns"]))
        for row in result["table"]["rows"][:15]:
            print(" | ".join(row))
    if result.get("chart"):
        print(f"\nГрафик: {result['chart']['type']}, точек: {len(result['chart']['categories'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
