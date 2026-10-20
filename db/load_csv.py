#!/usr/bin/env python3
"""Потоковая загрузка сырых CSV в PostgreSQL для локального тестирования.

Файлы не изменяются, все ячейки сохраняются в колонках типа TEXT.
Числа с десятичной запятой и даты преобразует уже аналитическая платформа.
"""

import argparse
import csv
import os
import re
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg import sql
from psycopg.types.json import Jsonb

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


def fit_identifier(name: str, max_bytes: int = 63) -> str:
    """PostgreSQL ограничивает идентификаторы 63 байтами (не символами)."""
    return name.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")


def table_name_for(path: Path) -> str:
    """Имя таблицы из имени исходного файла; не зависит от конкретного датасета."""
    stem = re.sub(r"\W+", "_", path.stem, flags=re.UNICODE).strip("_")
    if not stem:
        raise ValueError(f"Невозможно получить имя таблицы для {path}")
    return fit_identifier(f"data_{stem}")


def columns_for(headers: list[str]) -> list[str]:
    """Колонки сохраняют названия из CSV, а пустые/повторные имена уточняются."""
    if not headers:
        raise ValueError("CSV не содержит колонок")
    result: list[str] = []
    used: set[str] = set()
    for index, original in enumerate(headers, start=1):
        base = original.strip() or f"column_{index}"
        suffix_number = 1
        while True:
            suffix = "" if suffix_number == 1 else f"__{suffix_number}"
            candidate = fit_identifier(base, 63 - len(suffix.encode("utf-8"))) + suffix
            if candidate.casefold() not in used:
                used.add(candidate.casefold())
                result.append(candidate)
                break
            suffix_number += 1
    return result


def connect() -> psycopg.Connection:
    return psycopg.connect(
        host=os.getenv("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        dbname=os.getenv("POSTGRES_DB", "hackathon"),
        user=os.getenv("POSTGRES_USER", "bi_user"),
        password=os.getenv("POSTGRES_PASSWORD", ""),
        connect_timeout=10,
    )


def prepare(conn: psycopg.Connection, schema: str) -> None:
    conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
    conn.execute("CREATE SCHEMA IF NOT EXISTS bi_meta")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bi_meta.imports (
            schema_name TEXT NOT NULL,
            table_name TEXT NOT NULL,
            source_file TEXT NOT NULL,
            raw_headers JSONB NOT NULL,
            db_columns JSONB NOT NULL,
            row_count BIGINT NOT NULL,
            imported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (schema_name, table_name)
        )
        """
    )
    conn.commit()


def import_file(
    conn: psycopg.Connection,
    path: Path,
    root: Path,
    schema: str,
    replace: bool,
) -> int:
    table = table_name_for(path)
    qualified = sql.Identifier(schema, table)

    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.reader(file, delimiter=";", quotechar='"')
        headers = next(reader, None)
        if headers is None:
            raise ValueError(f"{path}: пустой CSV")
        columns = columns_for(headers)

        with conn.transaction():
            existing = conn.execute(
                """
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = %s AND table_name = %s
                """,
                (schema, table),
            ).fetchone()

            if existing and not replace:
                raise RuntimeError(
                    f"{schema}.{table} уже существует. "
                    "Для осознанной повторной загрузки передайте --replace."
                )

            if existing:
                # Без CASCADE: если от таблицы зависят другие объекты, не удаляем их.
                conn.execute(sql.SQL("DROP TABLE {}").format(qualified))

            definitions = sql.SQL(", ").join(
                sql.SQL("{} TEXT").format(sql.Identifier(column)) for column in columns
            )
            conn.execute(
                sql.SQL("CREATE TABLE {} ({})").format(qualified, definitions)
            )

            copy_query = sql.SQL("COPY {} ({}) FROM STDIN").format(
                qualified,
                sql.SQL(", ").join(sql.Identifier(column) for column in columns),
            )
            count = 0
            with conn.cursor() as cursor:
                with cursor.copy(copy_query) as copy:
                    for line_number, values in enumerate(reader, start=2):
                        if len(values) != len(headers):
                            raise ValueError(
                                f"{path}, запись {line_number}: "
                                f"ожидалось {len(headers)} полей, получено {len(values)}"
                            )
                        # Сохраняем строки как есть, включая десятичную запятую.
                        copy.write_row(values)
                        count += 1

            actual = conn.execute(
                sql.SQL("SELECT count(*) FROM {}").format(qualified)
            ).fetchone()[0]
            if actual != count:
                raise RuntimeError(
                    f"Проверка {schema}.{table}: загружено {actual}, ожидалось {count}"
                )

            conn.execute(
                """
                INSERT INTO bi_meta.imports (
                    schema_name, table_name, source_file,
                    raw_headers, db_columns, row_count
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (schema_name, table_name)
                DO UPDATE SET
                    source_file = EXCLUDED.source_file,
                    raw_headers = EXCLUDED.raw_headers,
                    db_columns = EXCLUDED.db_columns,
                    row_count = EXCLUDED.row_count,
                    imported_at = now()
                """,
                (
                    schema,
                    table,
                    str(path.relative_to(root)),
                    Jsonb(headers),
                    Jsonb(columns),
                    count,
                ),
            )
    return count


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Загрузить CSV (UTF-8, разделитель ;) в PostgreSQL без предобработки."
    )
    parser.add_argument(
        "--directory",
        type=Path,
        default=PROJECT_ROOT / "Исходники",
        help="Папка с CSV, по умолчанию ./Исходники",
    )
    parser.add_argument(
        "--schema",
        default=os.getenv("POSTGRES_SCHEMA", "raw"),
        help="Схема PostgreSQL для сырых таблиц",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Явно удалить и заново загрузить существующие таблицы",
    )
    args = parser.parse_args()

    root = args.directory.expanduser().resolve()
    if not root.is_dir():
        parser.error(f"Не найдена папка {root}. Сначала скачайте исходные CSV.")

    paths = sorted(path for path in root.rglob("*.csv") if path.is_file())
    if not paths:
        parser.error(f"Не найдены CSV в {root}")

    names = [table_name_for(path) for path in paths]
    if len(names) != len(set(names)):
        parser.error("Имена некоторых CSV дают одинаковые имена таблиц.")

    total = 0
    try:
        with connect() as conn:
            prepare(conn, args.schema)
            for path in paths:
                count = import_file(conn, path, root, args.schema, args.replace)
                total += count
                print(f"{path.relative_to(root)} -> {args.schema}.{table_name_for(path)}: {count} строк")
    except (psycopg.Error, OSError, ValueError, RuntimeError, csv.Error) as error:
        print(f"Ошибка импорта: {error}", file=sys.stderr)
        return 1

    print(f"Готово: {len(paths)} CSV, {total} строк. Оригинальные файлы не изменялись.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
