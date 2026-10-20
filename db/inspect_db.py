#!/usr/bin/env python3
"""Проверка подключения и просмотр произвольной схемы PostgreSQL."""

import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg import sql

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def main() -> int:
    schema = os.getenv("POSTGRES_SCHEMA", "raw")
    try:
        with psycopg.connect(
            host=os.getenv("POSTGRES_HOST", "127.0.0.1"),
            port=int(os.getenv("POSTGRES_PORT", "5432")),
            dbname=os.getenv("POSTGRES_DB", "hackathon"),
            user=os.getenv("POSTGRES_USER", "bi_user"),
            password=os.getenv("POSTGRES_PASSWORD", ""),
            connect_timeout=10,
        ) as conn:
            tables = conn.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = %s AND table_type = 'BASE TABLE'
                ORDER BY table_name
                """,
                (schema,),
            ).fetchall()
            print(f"Схема {schema}: {len(tables)} таблиц")
            for (table,) in tables:
                columns = conn.execute(
                    """
                    SELECT column_name, data_type
                    FROM information_schema.columns
                    WHERE table_schema = %s AND table_name = %s
                    ORDER BY ordinal_position
                    """,
                    (schema, table),
                ).fetchall()
                count = conn.execute(
                    sql.SQL("SELECT count(*) FROM {}").format(
                        sql.Identifier(schema, table)
                    )
                ).fetchone()[0]
                print(f"\n{schema}.{table} — {count} строк")
                for name, data_type in columns:
                    print(f"  {name}: {data_type}")
    except (psycopg.Error, ValueError) as error:
        print(f"Ошибка подключения или проверки: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
