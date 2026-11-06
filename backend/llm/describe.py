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
import datetime as dt
import json
import re
from collections import OrderedDict
from threading import Lock
import sys
from pathlib import Path

from .catalog_summary import (display_table_name, format_int, match_table_name, overview_table,
                              quality_section, relationships_section, summarize_catalog, table_block)

RULES = """Правила:
- Пиши по-русски, просто, для руководителя без технического опыта. Без SQL.
- Используй ТОЛЬКО то, что дано ниже. Не придумывай поля, цифры и периоды.
- Не пересказывай список колонок — объясняй смысл.
- Отвечай только по-русски; не вставляй иероглифы или случайные слова на других языках, если их нет в исходном каталоге."""

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


_UNEXPECTED_CJK = re.compile(r"[\\u3400-\\u9fff]")
_DESCRIPTION_CACHE: OrderedDict[tuple[str, str, str], str] = OrderedDict()
_DESCRIPTION_CACHE_LOCK = Lock()
_DESCRIPTION_CACHE_MAX = 128


def _description_fallback(table: dict) -> str:
    """Avoid displaying non-Russian hallucinations absent from the source schema."""
    name = display_table_name(table.get("name", ""))
    columns = [str(c["name"]) for c in table.get("columns") or []
               if isinstance(c, dict) and c.get("name")][:3]
    if columns:
        fields = ", ".join(f"«{c}»" for c in columns)
        return (f"Содержит записи таблицы «{name}». "
                f"Среди доступных для анализа полей: {fields}.")
    return f"Содержит данные таблицы «{name}». Для анализа нужны сведения о её полях."


def describe_table(client, table: dict) -> str:
    block = table_block(table)
    settings = getattr(client, "settings", None)
    model = getattr(settings, "model", None)
    base_url = getattr(settings, "base_url", None)
    # Cache only descriptions for explicitly identified model/provider; the
    # full catalog table block is part of the key, so schema changes invalidate.
    key = (base_url, model, block) if model and base_url else None
    if key is not None:
        with _DESCRIPTION_CACHE_LOCK:
            if key in _DESCRIPTION_CACHE:
                _DESCRIPTION_CACHE.move_to_end(key)
                return _DESCRIPTION_CACHE[key]
    raw = client.chat([
        {"role": "system", "content": TABLE_PROMPT},
        {"role": "user", "content": block},
    ], max_tokens=200)
    if _UNEXPECTED_CJK.search(raw) and not _UNEXPECTED_CJK.search(block):
        text = _description_fallback(table)
    else:
        text = shorten(raw, display_table_name(table["name"]))
    if key is not None:
        with _DESCRIPTION_CACHE_LOCK:
            _DESCRIPTION_CACHE[key] = text
            _DESCRIPTION_CACHE.move_to_end(key)
            if len(_DESCRIPTION_CACHE) > _DESCRIPTION_CACHE_MAX:
                _DESCRIPTION_CACHE.popitem(last=False)
    return text


def _fmt_date(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, dt.datetime):
        return value.strftime("%d.%m.%Y")
    if isinstance(value, dt.date):
        return value.strftime("%d.%m.%Y")
    return str(value)[:10]


def _as_date(value):
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def period_section(catalog: dict, runner=None) -> str:
    """Фактический охват данных по датам (#119): MIN/MAX каждого поля даты — по корректным
    значениям (тип в базе уже приведён), с числом пустых и некорректных значений из каталога.
    Значения кэшируются по источнику (PostgresRunner.date_range), профиль на каждый вопрос
    не пересчитывается. Ничего не выдумывается: нет поля даты или нет статистики — так и пишем."""
    from .sql_runner import column_kind

    lines, mins, maxs = [], [], []
    for table in catalog.get("tables") or []:
        name = display_table_name(table["name"])
        columns = table.get("columns") or []
        dates = [c for c in columns if column_kind(c) in ("date", "timestamp")
                 or ((c.get("conversion") or {}).get("date_range") or {}).get("scope")
                 == "full_column_valid_values"]
        unsure = [c["name"] for c in columns
                  if c.get("inferred_type") in ("date", "timestamp")
                  and column_kind(c) is None and c not in dates]
        if not dates:
            note = "нет поля даты — период определить нельзя"
            if unsure:
                note = ("поле даты неоднозначно (формат подтверждён не на всех строках): "
                        + ", ".join(f"«{u}»" for u in unsure) + " — период не рассчитан")
            lines.append(f"- «{name}»: {note}.")
            continue
        # Group date aggregates into one read-only SQL scan per table when the
        # runner supports batching. Older runners still use date_range().
        ranges = {}
        if runner is not None and hasattr(runner, "date_ranges"):
            try:
                verified = [c["name"] for c in dates
                            if column_kind(c) in ("date", "timestamp")]
                ranges = runner.date_ranges(table, verified)
            except Exception:
                ranges = {}
        parts = []
        for col in dates:
            quality = (col.get("conversion") or {}).get("quality") or {}
            total = quality.get("total_rows")
            missing = quality.get("missing_rows") or 0
            invalid = quality.get("invalid_rows") or 0
            low = high = None
            if col["name"] in ranges:
                low, high = ranges[col["name"]]
            elif runner is not None:
                try:
                    low, high = runner.date_range(table, col["name"])
                except Exception:
                    low = high = None
            audited = (col.get("conversion") or {}).get("date_range") or {}
            if (low is None or high is None) and audited.get("scope") == "full_column_valid_values":
                low, high = audited.get("min"), audited.get("max")
            if low is None and high is None:
                span = ("корректных дат нет" if audited.get("scope")
                        == "full_column_valid_values" else "статистика не рассчитана")
            else:
                span = f"с {_fmt_date(low)} по {_fmt_date(high)}"
                if (col.get("conversion") or {}).get("policy") == "safe_complete":
                    mins.append(_as_date(low))
                    maxs.append(_as_date(high))
            extra = []
            if total and missing:
                extra.append(f"заполнено {format_int(total - missing)} из {format_int(total)}")
            if invalid:
                extra.append(f"некорректных значений: {format_int(invalid)}")
            parts.append(f"«{col['name']}» {span}" + (f" ({'; '.join(extra)})" if extra else ""))
        lines.append(f"- «{name}»: " + "; ".join(parts) + ".")
    mins = [m for m in mins if m]
    maxs = [m for m in maxs if m]
    header = ["Период данных (по полям дат, только корректные значения):"]
    if len(catalog.get("tables") or []) > 1 and mins and maxs:
        header.append(
            f"- Общий диапазон значений полей даты: с {_fmt_date(min(mins))} "
            f"по {_fmt_date(max(maxs))}. "
            "Включает разные виды дат, в том числе плановые сроки и будущие дедлайны; "
            "это не период фактически совершённых операций. "
            "Период каждой таблицы и события смотрите ниже."
        )
    return "\n".join(header + lines)


def describe_data(catalog: dict, relationships: dict | None = None,
                  client=None, progress=None, table_name: str | None = None,
                  runner=None) -> dict:
    """Возвращает ответ в формате docs/api-contract.md: answer + table.
    runner — для фактических границ дат (#119); без него период честно помечается как
    не рассчитанный."""
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
        period_section(catalog, runner),
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
        print()
        print(period_section(catalog))
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
