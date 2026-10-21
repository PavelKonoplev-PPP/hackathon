#!/usr/bin/env python3
"""Безопасные SQL-выражения для приведения значений из сырых TEXT-колонок.

Использует PostgreSQL 16 и pg_input_is_valid(). Исходные таблицы не меняет.
Невалидные/пустые значения дают NULL; качество подсчитывается отдельно.
"""

import argparse
import json
import sys

import psycopg
from psycopg import sql

try:
    from .profile_db import connect
except ImportError:
    from profile_db import connect

# Не принимаем произвольные целевые типы из LLM: только явный whitelist.
CAST_TYPES = {
    "integer": ("bigint", r"^[+-]?[0-9]+$"),
    "decimal": ("numeric", r"^[+-]?([0-9]+([.][0-9]+)?|[.][0-9]+)$"),
    "date": ("date", r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"),
    "timestamp": (
        "timestamp without time zone",
        r"^[0-9]{4}-[0-9]{2}-[0-9]{2}[ T][0-9]{2}:[0-9]{2}(:[0-9]{2}([.][0-9]{1,6})?)?$",
    ),
}
RU_DATE_RE = r"^[0-9]{2}[.][0-9]{2}[.][0-9]{4}$"


def cast_parts(column: str, kind: str) -> tuple[sql.Composable, sql.Composable, sql.Composable, str]:
    """Возвращает (очищенное значение, нормализованное значение, условие, sql-тип).

    Важно: один из CAST_TYPES должен быть утверждён backend после профилирования.
    Коды/идентификаторы никогда не превращаем автоматически в число.
    """
    if kind not in CAST_TYPES:
        raise ValueError(f"Неподдерживаемый тип для преобразования: {kind}")
    if not column:
        raise ValueError("Имя колонки не может быть пустым")

    pg_type, pattern = CAST_TYPES[kind]
    source = sql.SQL("NULLIF(btrim({}::text), '')").format(sql.Identifier(column))
    if kind == "decimal":
        normalized = sql.SQL("replace({}, ',', '.')").format(source)
    elif kind == "date":
        # Не полагаемся на DateStyle: приводим ДД.ММ.ГГГГ к ISO.
        normalized = sql.SQL(
            "(CASE WHEN {} ~ {} THEN "
            "substr({}, 7, 4) || '-' || substr({}, 4, 2) || '-' || substr({}, 1, 2) "
            "ELSE {} END)"
        ).format(
            source, sql.Literal(RU_DATE_RE),
            source, source, source, source,
        )
    else:
        normalized = source

    valid = sql.SQL("({} ~ {} AND pg_input_is_valid({}, {}))").format(
        normalized, sql.Literal(pattern),
        normalized, sql.Literal(pg_type),
    )
    return source, normalized, valid, pg_type


def safe_cast_expression(column: str, kind: str) -> sql.Composable:
    """CASE-выражение для SELECT: некорректные значения становятся NULL."""
    _, normalized, valid, pg_type = cast_parts(column, kind)
    # тип вставляется из закрытого перечня CAST_TYPES, не из ответа LLM.
    target = sql.SQL(pg_type)
    return sql.SQL("(CASE WHEN {} THEN {}::{} ELSE NULL::{} END)").format(
        valid, normalized, target, target,
    )


def quality_query(schema: str, table: str, column: str, kind: str) -> sql.Composable:
    """Подсчёт пропусков и ошибок на всей таблице, без записи и без показа значений."""
    if not schema or not table:
        raise ValueError("Схема и таблица обязательны")
    source, _, valid, _ = cast_parts(column, kind)
    converted = safe_cast_expression(column, kind)
    return sql.SQL(
        "SELECT count(*) AS total_rows, "
        "count(*) FILTER (WHERE {} IS NULL) AS missing_rows, "
        "count(*) FILTER (WHERE {} IS NOT NULL AND NOT coalesce({}, false)) AS invalid_rows, "
        "count({}) AS convertible_rows "
        "FROM {}"
    ).format(
        source, source, valid, converted, sql.Identifier(schema, table)
    )


def audit_column(conn, schema: str, table: str, column: str, kind: str) -> dict:
    """Проверяет метаданные и безопасно агрегирует качество колонки."""
    if schema.startswith("pg_") or schema == "information_schema":
        raise ValueError("Профилирование системных схем запрещено")
    exists = conn.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = %s AND column_name = %s",
        (schema, table, column),
    ).fetchone()
    if exists is None:
        raise ValueError(f"Колонка {schema}.{table}.{column} недоступна")

    row = conn.execute(quality_query(schema, table, column, kind)).fetchone()
    return {
        "schema": schema,
        "table": table,
        "column": column,
        "target_type": kind,
        "total_rows": row[0],
        "missing_rows": row[1],
        "invalid_rows": row[2],
        "convertible_rows": row[3],
        "source_modified": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Проверить безопасное приведение колонки TEXT в PostgreSQL 16"
    )
    parser.add_argument("--schema", default="raw")
    parser.add_argument("--table", required=True)
    parser.add_argument("--column", required=True)
    parser.add_argument("--kind", required=True, choices=sorted(CAST_TYPES))
    args = parser.parse_args()

    try:
        with connect() as conn:
            result = audit_column(conn, args.schema, args.table, args.column, args.kind)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (psycopg.Error, ValueError) as error:
        print(f"Ошибка проверки преобразования: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
