"""Проверяемое сравнение среднедневной метрики на объект за календарные периоды.

Сырые TEXT-значения остаются без изменений. Даты и количества приводятся
через safe_cast, а число объектов считается независимо от дней с событиями.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date

import psycopg
from psycopg import sql

try:
    from .run_golden_checks import connect_readonly
    from .safe_cast import safe_cast_expression
except ImportError:
    from run_golden_checks import connect_readonly
    from safe_cast import safe_cast_expression


def calendar_days(start: date, end: date) -> int:
    """Число дней в полном календарном периоде [start, end)."""
    if not isinstance(start, date) or not isinstance(end, date):
        raise ValueError("Границы периода должны быть датами")
    if start.day != 1 or end.day != 1 or start >= end:
        raise ValueError("Период должен состоять из полных календарных месяцев")
    return (end - start).days


def comparison_sql(
    *,
    schema: str,
    table: str,
    group_column: str,
    entity_column: str,
    date_column: str,
    quantity_column: str,
    first_start: date,
    first_end: date,
    second_start: date,
    second_end: date,
    date_kind: str = "date",
    top_n: int | None = None,
) -> tuple[sql.Composable, tuple]:
    """Строит параметризованный SELECT для сравнения двух календарных периодов.

    Знаменатель в обоих периодах — одинаковое число уникальных непустых
    идентификаторов объектов во ВСЁМ источнике, умноженное на календарные дни.
    Это явное допущение о фиксированной совокупности объектов, не о числе
    объектов с продажами в конкретном месяце. Группы с нулевой базой
    получают NULL в проценте изменения, а не бесконечность.
    """
    if not all(isinstance(x, str) and x.strip() for x in (
        schema, table, group_column, entity_column, date_column, quantity_column
    )):
        raise ValueError("Схема, таблица и названия колонок обязательны")
    first_days = calendar_days(first_start, first_end)
    second_days = calendar_days(second_start, second_end)
    if first_end > second_start:
        raise ValueError("Периоды должны идти по порядку и не пересекаться")
    if date_kind not in ("date", "timestamp"):
        raise ValueError("Дата должна иметь тип date или timestamp")
    if top_n is not None and (type(top_n) is not int or not 1 <= top_n <= 200):
        raise ValueError("top_n должен быть целым от 1 до 200")

    group_expr = sql.SQL("NULLIF(BTRIM({}::text), '')").format(sql.Identifier(group_column))
    entity_expr = sql.SQL("NULLIF(BTRIM({}::text), '')").format(sql.Identifier(entity_column))
    date_expr = sql.SQL("({})::date").format(safe_cast_expression(date_column, date_kind))
    quantity_expr = safe_cast_expression(quantity_column, "decimal")
    limit_clause = sql.SQL("LIMIT %s") if top_n is not None else sql.SQL("")
    query = sql.SQL("""
        WITH bounds AS (
            SELECT %s::date AS first_start, %s::date AS first_end,
                   %s::date AS second_start, %s::date AS second_end,
                   %s::numeric AS first_days, %s::numeric AS second_days
        ),
        events AS (
            SELECT {group_expr} AS group_value,
                   {entity_expr} AS entity_value,
                   {date_expr} AS event_day,
                   {quantity_expr} AS quantity
            FROM {source}
        ),
        population AS (
            SELECT COUNT(DISTINCT entity_value) AS objects FROM events
        ),
        totals AS (
            SELECT group_value,
                   COALESCE(SUM(quantity) FILTER (
                       WHERE event_day >= b.first_start AND event_day < b.first_end
                   ), 0) AS first_total,
                   COALESCE(SUM(quantity) FILTER (
                       WHERE event_day >= b.second_start AND event_day < b.second_end
                   ), 0) AS second_total
            FROM events CROSS JOIN bounds b
            WHERE group_value IS NOT NULL
              AND ((event_day >= b.first_start AND event_day < b.first_end)
                OR (event_day >= b.second_start AND event_day < b.second_end))
            GROUP BY group_value
        )
        SELECT t.group_value AS "Группа",
               p.objects AS "Объектов",
               t.first_total AS "Количество_1",
               t.second_total AS "Количество_2",
               ROUND(t.first_total / NULLIF(p.objects * b.first_days, 0), 4)
                   AS "Среднее_1",
               ROUND(t.second_total / NULLIF(p.objects * b.second_days, 0), 4)
                   AS "Среднее_2",
               ROUND(100 * (
                   (t.second_total / b.second_days)
                   / NULLIF(t.first_total / b.first_days, 0) - 1
               ), 2) AS "Рост, %%"
        FROM totals t CROSS JOIN population p CROSS JOIN bounds b
        WHERE p.objects > 0
        ORDER BY "Рост, %%" DESC NULLS LAST, "Группа"
        {limit_clause}
    """).format(
        group_expr=group_expr,
        entity_expr=entity_expr,
        date_expr=date_expr,
        quantity_expr=quantity_expr,
        source=sql.Identifier(schema, table),
        limit_clause=limit_clause,
    )
    params = (
        first_start, first_end, second_start, second_end, first_days, second_days,
    )
    if top_n is not None:
        params += (top_n,)
    return query, params


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Сравнение среднедневного количества на объект, только чтение"
    )
    parser.add_argument("--schema", default="raw")
    parser.add_argument("--table", required=True)
    parser.add_argument("--group", required=True, dest="group_column")
    parser.add_argument("--entity", required=True, dest="entity_column")
    parser.add_argument("--date", required=True, dest="date_column")
    parser.add_argument("--quantity", required=True, dest="quantity_column")
    parser.add_argument("--date-kind", choices=["date", "timestamp"], default="date")
    for name in ("first-start", "first-end", "second-start", "second-end"):
        parser.add_argument("--" + name, required=True, type=date.fromisoformat)
    parser.add_argument("--top", type=int, default=3)
    args = parser.parse_args()

    try:
        query, params = comparison_sql(
            schema=args.schema,
            table=args.table,
            group_column=args.group_column,
            entity_column=args.entity_column,
            date_column=args.date_column,
            quantity_column=args.quantity_column,
            first_start=args.first_start,
            first_end=args.first_end,
            second_start=args.second_start,
            second_end=args.second_end,
            date_kind=args.date_kind,
            top_n=args.top,
        )
        with connect_readonly() as conn:
            with conn.transaction():
                conn.execute("SET TRANSACTION READ ONLY")
                conn.execute("SET LOCAL statement_timeout = '20000ms'")
                with conn.cursor() as cur:
                    cur.execute(query, params)
                    columns = [col.name for col in cur.description]
                    rows = [dict(zip(columns, row)) for row in cur.fetchall()]
        print(json.dumps({
            "days": {"first": params[4], "second": params[5]},
            "population_scope": "all_rows_in_source",
            "results": rows,
        }, ensure_ascii=False, indent=2, default=str))
        return 0
    except (ValueError, psycopg.Error) as exc:
        print(f"Ошибка расчёта: {str(exc).splitlines()[0]}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
