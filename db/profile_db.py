#!/usr/bin/env python3
"""Профиль неизвестных таблиц PostgreSQL: метаданные + ограниченная выборка.

Не изменяет базу и не делает полный COUNT(DISTINCT) по большим таблицам.
Результат — структурированный JSON для backend/LLM-агента.
"""
import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg import sql

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

DEFAULT_SAMPLE_SIZE = 200
MAX_SAMPLE_SIZE = 1000
MAX_TABLES = 100
EXAMPLE_LIMIT = 3
VALUE_LIMIT = 80

NUMBER_RE = re.compile(r"[+-]?(?:\d+(?:[.,]\d+)?|[.,]\d+)\Z")
ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
RU_DATE_RE = re.compile(r"\d{2}\.\d{2}\.\d{4}\Z")
TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}")
IDENTIFIER_RE = re.compile(
    r"(^id(?:_|$)|_id(?:_|$)|\bкод\b|\bартикул\b|\bидентификатор\b)",
    re.IGNORECASE,
)
BOOL_TEXT = {"да", "нет", "true", "false", "yes", "no"}
NATIVE_TYPES = {
    "smallint": "integer",
    "integer": "integer",
    "bigint": "integer",
    "numeric": "decimal",
    "decimal": "decimal",
    "real": "decimal",
    "double precision": "decimal",
    "date": "date",
    "timestamp without time zone": "timestamp",
    "timestamp with time zone": "timestamp",
    "boolean": "boolean",
    "uuid": "identifier",
}


def identifier_name(name: str) -> bool:
    return bool(IDENTIFIER_RE.search(name))


def parse_number(value: str) -> Decimal | None:
    value = value.strip()
    if not NUMBER_RE.fullmatch(value):
        return None
    try:
        return Decimal(value.replace(",", "."))
    except InvalidOperation:
        return None


def parse_date(value: str) -> date | None:
    value = value.strip()
    try:
        if ISO_DATE_RE.fullmatch(value):
            return date.fromisoformat(value)
        if RU_DATE_RE.fullmatch(value):
            return datetime.strptime(value, "%d.%m.%Y").date()
    except ValueError:
        pass
    return None


def parse_timestamp(value: str) -> datetime | None:
    value = value.strip()
    if not TIMESTAMP_RE.match(value):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _clean_preview(value: str) -> str:
    """Ограничиваем размер и исключаем переносы строк из примеров."""
    return value.replace("\r", " ").replace("\n", " ")[:VALUE_LIMIT]


def analyze_column(
    name: str, storage_type: str, values: list, *, include_examples: bool = True
) -> dict:
    """Определяет вероятный семантический тип по значениям одной колонки."""
    null_count = sum(value is None for value in values)
    blank_count = sum(isinstance(value, str) and not value.strip() for value in values)
    nonempty = [
        str(value).strip()
        for value in values
        if value is not None and (not isinstance(value, str) or value.strip())
    ]
    unique = list(dict.fromkeys(nonempty))
    result = {
        "inferred_type": "unknown",
        "confidence": 0.0,
        "sample_rows": len(values),
        "sample_null_count": null_count,
        "sample_blank_count": blank_count,
        "distinct_in_sample": len(unique),
        "examples": [_clean_preview(value) for value in unique[:EXAMPLE_LIMIT]]
        if include_examples else [],
        "sample_min": None,
        "sample_max": None,
        "numeric_format": None,
        "warning": None,
    }
    if not nonempty:
        return result

    native_type = NATIVE_TYPES.get(storage_type.lower())
    kind = None
    confidence = 1.0

    # Явные ключи по имени нельзя бездумно превратить в числа:
    # числовые ID и значения с ведущими нулями должны оставаться строками.
    if identifier_name(name) or (
        storage_type.lower() in {"text", "character varying", "character"}
        and any(re.fullmatch(r"0\d+", value) for value in nonempty)
    ):
        kind = "identifier"
    elif native_type:
        kind = native_type
    else:
        ratios = {
            "integer": sum(bool(re.fullmatch(r"[+-]?\d+", value)) for value in nonempty),
            "decimal": sum(parse_number(value) is not None for value in nonempty),
            "timestamp": sum(parse_timestamp(value) is not None for value in nonempty),
            "date": sum(parse_date(value) is not None for value in nonempty),
            "boolean": sum(value.casefold() in BOOL_TEXT for value in nonempty),
        }
        # Сперва специализированные типы, затем целое число и decimal.
        best_count = max(ratios.values())
        for candidate in ("timestamp", "date", "boolean", "integer", "decimal"):
            if ratios[candidate] == best_count and best_count:
                best_kind = candidate
                break
        else:
            best_kind = None
        confidence = best_count / len(nonempty) if best_count else 0.0
        if confidence >= 0.95 and best_kind:
            kind = best_kind
        elif confidence >= 0.50 and best_kind:
            kind = "mixed"
            result["warning"] = (
                f"В выборке только {best_count}/{len(nonempty)} значений "
                f"соответствуют типу {best_kind}; автоматический CAST небезопасен"
            )
        else:
            category_count = len(unique)
            if len(nonempty) >= 10 and category_count <= 100 and (
                category_count / len(nonempty) <= 0.5
            ):
                kind = "category"
                confidence = 0.8
            else:
                kind = "text"
                confidence = 0.7

    result["inferred_type"] = kind
    result["confidence"] = round(confidence, 3)
    if len(nonempty) < 10 and result["warning"] is None:
        result["warning"] = "Малая выборка: тип определён предварительно"

    if kind in {"integer", "decimal"}:
        parsed = [parse_number(value) for value in nonempty]
        numbers = [number for number in parsed if number is not None]
        if numbers:
            result["sample_min"] = str(min(numbers))
            result["sample_max"] = str(max(numbers))
        if any("," in value for value in nonempty):
            result["numeric_format"] = "decimal_comma"
        elif any("." in value for value in nonempty):
            result["numeric_format"] = "decimal_dot"
        else:
            result["numeric_format"] = "integer"
    elif kind in {"date", "timestamp"}:
        parser = parse_date if kind == "date" else parse_timestamp
        parsed = [parser(value) for value in nonempty]
        normalized = []
        for value in parsed:
            if value is not None:
                if isinstance(value, datetime) and value.tzinfo is not None:
                    value = value.astimezone(timezone.utc)
                normalized.append(value.isoformat())
        if normalized:
            result["sample_min"] = min(normalized)
            result["sample_max"] = max(normalized)
    return result


def get_tables(conn, schema: str, max_tables: int) -> list[tuple]:
    """Список доступных обычных таблиц и оценки размера из pg_class."""
    query = """
        SELECT t.table_name, c.reltuples::bigint
        FROM information_schema.tables AS t
        LEFT JOIN pg_catalog.pg_namespace AS n
          ON n.nspname = t.table_schema
        LEFT JOIN pg_catalog.pg_class AS c
          ON c.relnamespace = n.oid AND c.relname = t.table_name
          AND c.relkind IN ('r', 'p')
        WHERE t.table_schema = %s AND t.table_type = 'BASE TABLE'
        ORDER BY t.table_name LIMIT %s
    """
    return conn.execute(query, (schema, max_tables)).fetchall()


def get_columns(conn, schema: str, table: str) -> list[tuple]:
    return conn.execute(
        """
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        ORDER BY ordinal_position
        """,
        (schema, table),
    ).fetchall()


def profile_database(
    conn,
    schema: str = "raw",
    table: str | None = None,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
    max_tables: int = MAX_TABLES,
    include_examples: bool = True,
) -> dict:
    """Не выполняет записи; каждая таблица считывается с LIMIT."""
    if not 1 <= sample_size <= MAX_SAMPLE_SIZE:
        raise ValueError(f"sample_size должен быть от 1 до {MAX_SAMPLE_SIZE}")
    if not 1 <= max_tables <= MAX_TABLES:
        raise ValueError(f"max_tables должен быть от 1 до {MAX_TABLES}")
    if schema == "information_schema" or schema.startswith("pg_"):
        raise ValueError("Системные схемы запрещены для профилирования")

    known_tables = get_tables(conn, schema, max_tables)
    if table is not None:
        selected = [row for row in known_tables if row[0] == table]
        if not selected:
            raise ValueError(
                f"Таблица {schema}.{table} не найдена среди доступных таблиц"
            )
    else:
        selected = known_tables

    report = {
        "schema": schema,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "sampling": {
            "method": "first_rows",
            "max_rows_per_table": sample_size,
            "statistics_are_sample_only": True,
        },
        "tables": [],
    }

    for table_name, estimated in selected:
        columns = get_columns(conn, schema, table_name)
        # Идентификаторы экранируются отдельно; имя таблицы не подставляется
        # в SQL через f-строку и не может превратиться в SQL-инъекцию.
        rows = conn.execute(
            sql.SQL("SELECT * FROM {} LIMIT %s").format(
                sql.Identifier(schema, table_name)
            ),
            (sample_size,),
        ).fetchall()
        column_profiles = []
        for index, (column_name, storage_type, nullable) in enumerate(columns):
            stats = analyze_column(
                column_name,
                storage_type,
                [row[index] for row in rows],
                include_examples=include_examples,
            )
            column_profiles.append({
                "name": column_name,
                "postgres_type": storage_type,
                "nullable": nullable == "YES",
                **stats,
            })
        report["tables"].append({
            "name": table_name,
            "estimated_total_rows": estimated if estimated is not None and estimated >= 0 else None,
            "sampled_rows": len(rows),
            "columns": column_profiles,
        })
    return report


def connect():
    return psycopg.connect(
        host=os.getenv("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        dbname=os.getenv("POSTGRES_DB", "hackathon"),
        user=os.getenv("POSTGRES_USER", "bi_user"),
        password=os.getenv("POSTGRES_PASSWORD", ""),
        connect_timeout=10,
        autocommit=True,
        # Даже при ошибочной настройке пользователя модуль не может писать.
        options="-c default_transaction_read_only=on -c statement_timeout=15000",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Исследовать неизвестные PostgreSQL-таблицы и получить JSON."
    )
    parser.add_argument("--schema", default=os.getenv("POSTGRES_SCHEMA", "raw"))
    parser.add_argument("--table", help="Исследовать только одну таблицу")
    parser.add_argument("--sample-size", type=int, default=DEFAULT_SAMPLE_SIZE)
    parser.add_argument("--max-tables", type=int, default=MAX_TABLES)
    parser.add_argument("--no-examples", action="store_true",
                        help="Не включать значения строк в результат (для приватных данных)")
    parser.add_argument("--output", type=Path,
                        help="Записать JSON в файл вместо вывода в терминал")
    args = parser.parse_args()

    try:
        with connect() as conn:
            result = profile_database(
                conn, schema=args.schema, table=args.table,
                sample_size=args.sample_size, max_tables=args.max_tables,
                include_examples=not args.no_examples,
            )
        payload = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(payload + "\n", encoding="utf-8")
            print(f"Профиль сохранён: {args.output}; таблиц: {len(result['tables'])}")
        else:
            print(payload)
    except (psycopg.Error, ValueError, OSError) as error:
        print(f"Ошибка профилирования: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
