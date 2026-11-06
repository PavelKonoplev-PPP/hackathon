"""Read-only validation of segmented metrics and IQR outlier counts.

SQL identifiers are always quoted. Values and date boundaries are parameters.
Raw tables are not changed; unsupported numerical values become NULL.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date

import psycopg
from psycopg import sql

try:
    from .safe_cast import safe_cast_expression
    from .run_golden_checks import connect_readonly
except ImportError:
    from safe_cast import safe_cast_expression
    from run_golden_checks import connect_readonly


def _validated_source(schema: str, table: str, segments: list[str], metric: str) -> None:
    fields = (schema, table, metric, *segments)
    if not all(isinstance(v, str) and v.strip() for v in fields):
        raise ValueError("Схема, таблица, показатель и признаки обязательны")
    if schema == "information_schema" or schema.lower().startswith("pg_"):
        raise ValueError("Системные схемы не поддерживаются")
    if not 1 <= len(segments) <= 3 or len(set(segments)) != len(segments):
        raise ValueError("Нужно от 1 до 3 разных признаков группировки")


def _limit(value: int | None) -> None:
    if value is not None and (type(value) is not int or not 1 <= value <= 200):
        raise ValueError("Лимит должен быть целым числом от 1 до 200")


def _min_sample(value: int) -> None:
    if type(value) is not int or value < 4:
        raise ValueError("Минимальный размер группы должен быть целым числом от 4")


def _valid_period(start: date, end: date) -> None:
    if type(start) is not date or type(end) is not date or start >= end:
        raise ValueError("Период должен иметь корректные границы [начало, конец)")


def _date_expression(column: str, kind: str) -> sql.Composable:
    if not isinstance(column, str) or not column.strip():
        raise ValueError("Колонка даты обязательна")
    if kind not in ("date", "timestamp"):
        raise ValueError("Дата должна иметь тип date или timestamp")
    return sql.SQL("({})::date").format(safe_cast_expression(column, kind))


def _segments(segments: list[str]):
    aliases = [f"seg_{i}" for i in range(1, len(segments) + 1)]
    fields = sql.SQL(", ").join(
        sql.SQL("{} AS {}").format(
            sql.SQL("NULLIF(BTRIM({}::text), '')").format(sql.Identifier(col)),
            sql.Identifier(alias),
        )
        for col, alias in zip(segments, aliases)
    )
    grouped = sql.SQL(", ").join(sql.Identifier(alias) for alias in aliases)
    not_null = sql.SQL(" AND ").join(
        sql.SQL("{} IS NOT NULL").format(sql.Identifier(alias))
        for alias in aliases
    )
    return aliases, fields, grouped, not_null


def outlier_sql(
    *,
    schema: str,
    table: str,
    segments: list[str],
    metric: str,
    min_sample: int = 8,
    iqr_multiplier: float = 1.5,
    date_column: str | None = None,
    date_kind: str = "date",
    start: date | None = None,
    end: date | None = None,
    limit: int | None = 100,
) -> tuple[sql.Composable, tuple]:
    """Tukey fences per 1–3 categorical dimensions, with sample-size checks.

    Groups below min_sample receive NULL outlier counts, not a spurious zero.
    Counts of omitted numeric values include empty and malformed values.
    """
    _validated_source(schema, table, segments, metric)
    _limit(limit)
    _min_sample(min_sample)
    if isinstance(iqr_multiplier, bool) or not isinstance(iqr_multiplier, (float, int)):
        raise ValueError("Неверный множитель IQR")
    if not math.isfinite(iqr_multiplier) or iqr_multiplier <= 0:
        raise ValueError("Множитель IQR должен быть конечным положительным числом")
    if (start is None) != (end is None):
        raise ValueError("Укажите обе границы периода")
    if date_column is None and (start is not None or end is not None):
        raise ValueError("Нужна колонка даты")
    if date_column is not None and (start is None or end is None):
        raise ValueError("Для фильтра даты нужны обе границы")
    if date_column is not None:
        _valid_period(start, end)
        d = _date_expression(date_column, date_kind)
        where_period = sql.SQL("WHERE {} >= %s::date AND {} < %s::date").format(d, d)
    else:
        where_period = sql.SQL("")

    aliases, fields, grouped, not_null = _segments(segments)
    source = sql.Identifier(schema, table)
    metric_expr = safe_cast_expression(metric, "decimal")
    join_conditions = sql.SQL(" AND ").join(
        sql.SQL("b.{0} = q.{0}").format(sql.Identifier(a)) for a in aliases
    )
    b_fields = sql.SQL(", ").join(
        sql.SQL("b.{}").format(sql.Identifier(a)) for a in aliases
    )
    f_fields = sql.SQL(", ").join(
        sql.SQL("f.{} AS {}").format(sql.Identifier(a), sql.Identifier(name))
        for a, name in zip(aliases, segments)
    )
    f_grouped = sql.SQL(", ").join(
        sql.SQL("f.{}").format(sql.Identifier(a)) for a in aliases
    )
    limit_sql = sql.SQL("LIMIT %s") if limit is not None else sql.SQL("")
    statement = sql.SQL("""
        WITH settings AS (
            SELECT %s::integer AS min_sample, %s::double precision AS multiplier
        ),
        sample AS (
            SELECT {fields}, {metric_expr} AS measure
            FROM {source}
            {where_period}
        ),
        quartiles AS (
            SELECT {grouped},
                   COUNT(*) AS row_count,
                   COUNT(measure) AS valid_count,
                   PERCENTILE_CONT(0.25) WITHIN GROUP (
                       ORDER BY measure::double precision
                   ) AS q1,
                   PERCENTILE_CONT(0.75) WITHIN GROUP (
                       ORDER BY measure::double precision
                   ) AS q3
            FROM sample
            WHERE {not_null}
            GROUP BY {grouped}
        ),
        flags AS (
            SELECT {b_fields}, b.measure, q.row_count, q.valid_count, q.q1, q.q3,
                   CASE WHEN q.valid_count >= cfg.min_sample
                          AND b.measure IS NOT NULL
                          AND (
                            b.measure::double precision <
                              q.q1 - (q.q3 - q.q1) * cfg.multiplier
                            OR b.measure::double precision >
                              q.q3 + (q.q3 - q.q1) * cfg.multiplier
                          )
                        THEN 1 ELSE 0 END AS is_outlier
            FROM sample b JOIN quartiles q ON {join_conditions}
            CROSS JOIN settings cfg
        )
        SELECT {f_fields},
               MAX(f.row_count) AS "Всего записей",
               MAX(f.valid_count) AS "Числовых значений",
               MAX(f.row_count) - MAX(f.valid_count) AS "Пропусков или ошибок",
               MAX(f.q1) AS "Q1",
               MAX(f.q3) AS "Q3",
               CASE WHEN MAX(f.valid_count) >= (SELECT min_sample FROM settings)
                    THEN SUM(f.is_outlier)::bigint ELSE NULL END AS "Выбросов",
               MIN(CASE WHEN f.is_outlier = 1 THEN f.measure END) AS "Мин. выброс",
               MAX(CASE WHEN f.is_outlier = 1 THEN f.measure END) AS "Макс. выброс",
               CASE WHEN MAX(f.valid_count) >= (SELECT min_sample FROM settings)
                    THEN 'checked' ELSE 'insufficient_sample' END AS "Статус"
        FROM flags f
        GROUP BY {f_grouped}
        ORDER BY "Выбросов" DESC NULLS LAST, "Числовых значений" DESC
        {limit_sql}
    """).format(
        fields=fields, metric_expr=metric_expr, source=source,
        where_period=where_period, grouped=grouped, not_null=not_null,
        b_fields=b_fields, join_conditions=join_conditions,
        f_fields=f_fields, f_grouped=f_grouped, limit_sql=limit_sql,
    )
    params: tuple = (min_sample, float(iqr_multiplier))
    if start is not None:
        params += (start, end)
    if limit is not None:
        params += (limit,)
    return statement, params


def segment_comparison_sql(
    *,
    schema: str,
    table: str,
    segments: list[str],
    metric: str,
    date_column: str,
    first_start: date,
    first_end: date,
    second_start: date,
    second_end: date,
    date_kind: str = "date",
    min_sample: int = 4,
    limit: int | None = 100,
) -> tuple[sql.Composable, tuple]:
    """Average, count, sum and change for combinations of categorical segments.

    Both period averages are shown only when each period has enough valid rows.
    Missing dates are excluded from period metrics; malformed numeric values
    count as skipped observations, not zero.
    """
    _validated_source(schema, table, segments, metric)
    _limit(limit)
    _min_sample(min_sample)
    _valid_period(first_start, first_end)
    _valid_period(second_start, second_end)
    if first_end > second_start:
        raise ValueError("Периоды не должны пересекаться и должны идти по порядку")
    d = _date_expression(date_column, date_kind)
    aliases, fields, grouped, not_null = _segments(segments)
    metric_expr = safe_cast_expression(metric, "decimal")
    displayed_fields = sql.SQL(", ").join(
        sql.SQL("g.{} AS {}").format(sql.Identifier(a), sql.Identifier(name))
        for a, name in zip(aliases, segments)
    )
    ordering = sql.SQL(", ").join(
        sql.SQL("g.{}").format(sql.Identifier(a)) for a in aliases
    )
    first = sql.SQL("event_day >= bounds.start_1 AND event_day < bounds.end_1")
    second = sql.SQL("event_day >= bounds.start_2 AND event_day < bounds.end_2")
    limit_sql = sql.SQL("LIMIT %s") if limit is not None else sql.SQL("")
    statement = sql.SQL("""
        WITH settings AS (SELECT %s::integer AS min_sample),
        bounds AS (
            SELECT %s::date AS start_1, %s::date AS end_1,
                   %s::date AS start_2, %s::date AS end_2
        ),
        typed AS (
            SELECT {fields}, {metric_expr} AS measure, {date_expr} AS event_day
            FROM {source}
        ),
        grouped AS (
            SELECT {grouped},
                   COUNT(*) FILTER (WHERE {first}) AS rows_1,
                   COUNT(*) FILTER (WHERE {second}) AS rows_2,
                   COUNT(measure) FILTER (WHERE {first}) AS valid_1,
                   COUNT(measure) FILTER (WHERE {second}) AS valid_2,
                   AVG(measure) FILTER (WHERE {first}) AS avg_1,
                   AVG(measure) FILTER (WHERE {second}) AS avg_2,
                   SUM(measure) FILTER (WHERE {first}) AS sum_1,
                   SUM(measure) FILTER (WHERE {second}) AS sum_2
            FROM typed CROSS JOIN bounds
            WHERE {not_null}
              AND (({first}) OR ({second}))
            GROUP BY {grouped}
        )
        SELECT {displayed_fields},
               g.rows_1 AS "Записей 1", g.valid_1 AS "Числовых 1",
               g.rows_1 - g.valid_1 AS "Пропусков или ошибок 1",
               g.rows_2 AS "Записей 2", g.valid_2 AS "Числовых 2",
               g.rows_2 - g.valid_2 AS "Пропусков или ошибок 2",
               g.sum_1 AS "Сумма 1", g.sum_2 AS "Сумма 2",
               CASE WHEN g.valid_1 >= cfg.min_sample
                    THEN ROUND(g.avg_1, 4) END AS "Среднее 1",
               CASE WHEN g.valid_2 >= cfg.min_sample
                    THEN ROUND(g.avg_2, 4) END AS "Среднее 2",
               CASE WHEN g.valid_1 >= cfg.min_sample AND g.valid_2 >= cfg.min_sample
                    THEN ROUND(g.avg_2 - g.avg_1, 4) END AS "Изменение",
               CASE WHEN g.valid_1 >= cfg.min_sample AND g.valid_2 >= cfg.min_sample
                    THEN ROUND(100 * (g.avg_2 / NULLIF(g.avg_1, 0) - 1), 2)
                    END AS "Изменение, %%",
               CASE WHEN g.valid_1 >= cfg.min_sample AND g.valid_2 >= cfg.min_sample
                    THEN 'checked' ELSE 'insufficient_sample' END AS "Статус"
        FROM grouped g CROSS JOIN settings cfg
        ORDER BY {ordering}
        {limit_sql}
    """).format(
        fields=fields, metric_expr=metric_expr, date_expr=d,
        source=sql.Identifier(schema, table),
        grouped=grouped, not_null=not_null,
        first=first, second=second,
        displayed_fields=displayed_fields, ordering=ordering, limit_sql=limit_sql,
    )
    params: tuple = (min_sample, first_start, first_end, second_start, second_end)
    if limit is not None:
        params += (limit,)
    return statement, params


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only QA of segments and outliers")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("outliers", "compare"):
        option = sub.add_parser(command)
        option.add_argument("--schema", default="raw")
        option.add_argument("--table", required=True)
        option.add_argument("--segment", action="append", required=True,
                            help="Repeat for a second or third dimension")
        option.add_argument("--metric", required=True)
        option.add_argument("--min-sample", type=int, default=8 if command == "outliers" else 4)
        option.add_argument("--limit", type=int, default=100)
        if command == "outliers":
            option.add_argument("--iqr-multiplier", type=float, default=1.5)
            option.add_argument("--date-column")
            option.add_argument("--start", type=date.fromisoformat)
            option.add_argument("--end", type=date.fromisoformat)
        else:
            option.add_argument("--date-column", required=True)
            for name in ("first-start", "first-end", "second-start", "second-end"):
                option.add_argument("--" + name, type=date.fromisoformat, required=True)
        option.add_argument("--date-kind", choices=("date", "timestamp"), default="date")
    args = parser.parse_args()
    try:
        shared = dict(
            schema=args.schema, table=args.table, segments=args.segment,
            metric=args.metric, min_sample=args.min_sample, limit=args.limit,
        )
        if args.command == "outliers":
            query, params = outlier_sql(
                **shared, iqr_multiplier=args.iqr_multiplier,
                date_column=args.date_column, date_kind=args.date_kind,
                start=args.start, end=args.end,
            )
        else:
            query, params = segment_comparison_sql(
                **shared, date_column=args.date_column, date_kind=args.date_kind,
                first_start=args.first_start, first_end=args.first_end,
                second_start=args.second_start, second_end=args.second_end,
            )
        with connect_readonly() as conn:
            with conn.transaction():
                conn.execute("SET TRANSACTION READ ONLY")
                conn.execute("SET LOCAL statement_timeout = '20000ms'")
                with conn.cursor() as cursor:
                    cursor.execute(query, params)
                    columns = [col.name for col in cursor.description]
                    rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        print(json.dumps({
            "method": "Tukey_IQR" if args.command == "outliers" else "two_period_segment_comparison",
            "segment_columns": args.segment,
            "metric_column": args.metric,
            "results": rows,
        }, ensure_ascii=False, default=str, indent=2))
        return 0
    except (ValueError, psycopg.Error) as exc:
        print(f"Ошибка проверки: {str(exc).splitlines()[0]}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
