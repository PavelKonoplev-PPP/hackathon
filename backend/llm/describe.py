"""Сценарий 1 (issue #3): описание неизвестных данных простым языком.

Как устроено (чтобы маленькая модель не путала факты):
1. Каждую таблицу модель описывает отдельно, видя только её колонки.
2. Общую картину и примеры вопросов модель пишет по этим коротким описаниям.
3. Раздел о качестве данных собирает код — цифры берутся прямо из каталога.

Запуск из корня проекта:
    python -m backend.llm.describe --catalog data/schema-catalog.json \
        --relationships data/relationship-catalog.json

Без модели (посмотреть сводку, которая уходит в промпты):
    python -m backend.llm.describe --catalog data/schema-catalog.json --dry-run
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from .catalog_summary import (display_table_name, match_table_name, overview_table, quality_section,
                              relationships_section, summarize_catalog, table_block)

RULES = """Правила:
- Пиши по-русски, просто, для руководителя без технического опыта. Без SQL.
- Используй ТОЛЬКО то, что дано ниже. Не придумывай поля, цифры и периоды.
- Не пересказывай список колонок — объясняй смысл."""

TABLE_PROMPT = f"""Ты — аналитик данных. Тебе дали описание одной таблицы из незнакомой базы.
Напиши 2 предложения: 1) что хранит эта таблица (о каком процессе бизнеса);
2) что по ней можно анализировать — назови 2–3 главных показателя и 2–3 разреза
(по каким полям группировать). Только эти 2 предложения, без заголовков.
{RULES}"""

OVERVIEW_PROMPT = f"""Ты — аналитик данных. Тебе дали краткие описания всех таблиц базы.
Напиши ответ строго в таком формате:

Общая картина: <2–3 предложения: о чём эти данные в целом, какие области бизнеса покрывают>

Что можно спросить:
1. <вопрос>
2. <вопрос>
3. <вопрос>
4. <вопрос>
5. <вопрос>

Вопросы должны быть конкретными, к разным таблицам, и отвечать на них можно по
показателям и разрезам из описаний (например: «Какой канал продаж приносит больше
всего выручки?»).
{RULES}"""


SINGLE_TABLE_OVERVIEW_PROMPT = f"""Ты — аналитик данных. Тебе дано описание таблицы «{{name}}».
Напиши ответ строго в таком формате:

Общая картина: <2–3 предложения: о чём эта таблица, какие бизнес-процессы она отражает>

Что можно спросить:
1. <вопрос>
2. <вопрос>
3. <вопрос>
4. <вопрос>
5. <вопрос>

Вопросы должны быть конкретными, строго к таблице «{{name}}», и отвечать на них можно по
показателям и разрезам из её описания (например: «Какой канал продаж приносит больше всего выручки?»).
{RULES}"""


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def shorten(text: str, name: str, max_sentences: int = 2) -> str:
    """Маленькие модели многословны: оставляем первые предложения и убираем
    повтор названия («Таблица «Продажи» хранит...» -> «Хранит...»)."""
    text = " ".join(text.split())
    sentences = [s for s in _SENTENCE_END.split(text) if s][:max_sentences]
    text = " ".join(sentences)
    for prefix in (f"Таблица «{name}» ", f"Таблица \"{name}\" ", f"Таблица {name} "):
        if text.startswith(prefix):
            rest = text[len(prefix):]
            text = rest[:1].upper() + rest[1:]
            break
    return text


def describe_table(client, table: dict) -> str:
    text = client.chat([
        {"role": "system", "content": TABLE_PROMPT},
        {"role": "user", "content": table_block(table)},
    ], max_tokens=200)
    return shorten(text, display_table_name(table["name"]))


def describe_data(catalog: dict, relationships: dict | None = None,
                  client=None, progress=None, table_name: str | None = None) -> dict:
    """Возвращает ответ в формате docs/api-contract.md: answer + table."""
    if client is None:
        from .client import LLMClient
        client = LLMClient()
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
                "table": None,
                "chart": None,
                "sql": None,
                "clarification": None,
            }
        tables = matched
        catalog = {**catalog, "tables": tables}

    parts = []
    for i, table in enumerate(tables, 1):
        name = display_table_name(table["name"])
        if progress:
            progress(f"[{i}/{len(tables)}] {name}")
        parts.append((name, describe_table(client, table)))

    short = "\n".join(f"- «{name}»: {text}" for name, text in parts)
    if progress:
        progress("Общая картина и примеры вопросов")

    if len(tables) == 1:
        tbl_name = display_table_name(tables[0]["name"])
        sys_prompt = SINGLE_TABLE_OVERVIEW_PROMPT.format(name=tbl_name)
        user_msg = f"Таблица «{tbl_name}»:\n\n{short}"
    else:
        sys_prompt = OVERVIEW_PROMPT
        user_msg = f"Таблиц: {len(tables)}.\n\n{short}"

    overview = client.chat([
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_msg},
    ], max_tokens=700).strip()

    links = relationships_section(relationships) if relationships else ""

    sections = [
        overview.split("Что можно спросить")[0].strip(),
        "Таблицы:\n" + "\n".join(f"{i}. «{name}» — {text}"
                                  for i, (name, text) in enumerate(parts, 1)),
        quality_section(catalog),
    ]
    if links:
        sections.append(links)
    if "Что можно спросить" in overview:
        sections.append("Что можно спросить" + overview.split("Что можно спросить", 1)[1])
    return {"answer": "\n\n".join(s for s in sections if s), "table": overview_table(catalog)}


def _read_json(path: Path | None) -> dict | None:
    if path is None:
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Описание данных простым языком")
    parser.add_argument("--catalog", type=Path, default=Path("data/schema-catalog.json"))
    parser.add_argument("--relationships", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true",
                        help="Показать сводку и раздел качества, не вызывая модель")
    args = parser.parse_args()
    try:
        catalog = _read_json(args.catalog)
        relationships = _read_json(args.relationships)
    except (OSError, ValueError) as exc:
        print(f"Не удалось прочитать каталог: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(summarize_catalog(catalog, relationships))
        print()
        print(quality_section(catalog))
        return 0

    result = describe_data(catalog, relationships,
                           progress=lambda msg: print(f"... {msg}", file=sys.stderr))
    print(result["answer"])
    print()
    print(" | ".join(result["table"]["columns"]))
    for row in result["table"]["rows"]:
        print(" | ".join(row))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
