"""Read-only PostgreSQL regression for six expert analytical questions.

Reference figures come from TestCases.md. This runner verifies the independent
SQL results on the currently connected dataset; it does not test the LLM.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg

try:
    from .run_golden_checks import connect_readonly
except ImportError:
    from run_golden_checks import connect_readonly


CASES = [
    {
        "id": "expert_01_drug_daily_growth",
        "description": "Дневные продажи на аптеку, февраль/март 2025",
        "sql": """
            WITH d AS (
              SELECT "Дата"::date AS dt, "ID_Аптеки" ph,
                     "ID_Лекарства" drug,
                     replace("Сколько_Купили", ',', '.')::numeric qty
              FROM raw."data_09_Лекарственные_препараты"
            ), ph AS (
              SELECT COUNT(DISTINCT ph) n FROM d
            ), m AS (
              SELECT drug,
                SUM(qty) FILTER (WHERE dt >= DATE '2025-02-01'
                  AND dt < DATE '2025-03-01') feb,
                SUM(qty) FILTER (WHERE dt >= DATE '2025-03-01'
                  AND dt < DATE '2025-04-01') mar
              FROM d GROUP BY drug
            )
            SELECT drug, ROUND(feb / (ph.n * 28.0), 4) feb_daily,
                   ROUND(mar / (ph.n * 31.0), 4) mar_daily,
                   ROUND(100 * ((mar / 31.0) /
                     NULLIF(feb / 28.0, 0) - 1), 2) growth_pct,
                   ph.n pharmacy_count
            FROM m CROSS JOIN ph
            WHERE feb IS NOT NULL AND mar IS NOT NULL AND ph.n > 0
            ORDER BY growth_pct DESC NULLS LAST, drug
            LIMIT 1
        """,
        "columns": ["drug", "feb_daily", "mar_daily", "growth_pct", "pharmacy_count"],
        "expected": ["1019", "1.5464", "2.0153", "30.32", 40],
    },
    {
        "id": "expert_02_city_net_growth",
        "description": "Рост чистой выручки по городам, февраль/март 2025",
        "sql": """
            WITH s AS (
              SELECT "Город" city, "Дата операции (МСК)"::timestamp ts,
                     replace("Количество, шт.", ',', '.')::numeric
                       * replace("Цена до скидки за единицу, руб.", ',', '.')::numeric
                       * (1 - replace("Скидка, %", ',', '.')::numeric / 100) net
              FROM raw."data_01_Продажи"
            ), m AS (
              SELECT city,
                SUM(net) FILTER (WHERE ts >= TIMESTAMP '2025-02-01'
                  AND ts < TIMESTAMP '2025-03-01') feb,
                SUM(net) FILTER (WHERE ts >= TIMESTAMP '2025-03-01'
                  AND ts < TIMESTAMP '2025-04-01') mar
              FROM s GROUP BY city
            )
            SELECT city, ROUND(feb, 2), ROUND(mar, 2),
                   ROUND(100 * (mar / NULLIF(feb, 0) - 1), 2) growth_pct
            FROM m WHERE feb IS NOT NULL AND mar IS NOT NULL
            ORDER BY growth_pct DESC NULLS LAST, city
            LIMIT 1
        """,
        "columns": ["city", "feb_revenue", "mar_revenue", "growth_pct"],
        "expected": ["Калининград", "1627110.35", "2640589.14", "62.29"],
    },
    {
        "id": "expert_03_support_p90",
        "description": "P90 времени решения обращений марта 2025, группы от 100",
        "sql": """
            WITH s AS (
              SELECT "Группа поддержки" grp,
                     EXTRACT(EPOCH FROM (
                       "Закрыто (МСК)"::timestamp - "Создано (МСК)"::timestamp
                     )) / 3600 AS hours
              FROM raw."data_02_Техническая_поддержка"
              WHERE NULLIF("Закрыто (МСК)", '')::timestamp >= TIMESTAMP '2025-03-01'
                AND NULLIF("Закрыто (МСК)", '')::timestamp < TIMESTAMP '2025-04-01'
            )
            SELECT grp, COUNT(*) n,
                   ROUND(PERCENTILE_CONT(0.9) WITHIN GROUP
                     (ORDER BY hours)::numeric, 2) p90_hours
            FROM s GROUP BY grp HAVING COUNT(*) >= 100
            ORDER BY p90_hours DESC NULLS LAST, grp LIMIT 1
        """,
        "columns": ["group", "cases", "p90_hours"],
        "expected": ["Доставка заказов", 4515, "72.49"],
    },
    {
        "id": "expert_04_delivery_p95",
        "description": "P95 положительной задержки доставленных отправлений марта 2025",
        "sql": """
            WITH s AS (
              SELECT "Перевозчик" carrier,
                     GREATEST(0, EXTRACT(EPOCH FROM (
                       "Доставлено (МСК)"::timestamp -
                       "Обещанный срок (МСК)"::timestamp
                     )) / 3600) delay_hours
              FROM raw."data_04_Доставка"
              WHERE "Статус" = 'Доставлено'
                AND NULLIF("Доставлено (МСК)", '')::timestamp >= TIMESTAMP '2025-03-01'
                AND NULLIF("Доставлено (МСК)", '')::timestamp < TIMESTAMP '2025-04-01'
            )
            SELECT carrier, COUNT(*) n,
                   ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP
                     (ORDER BY delay_hours)::numeric, 2) p95_hours
            FROM s GROUP BY carrier
            ORDER BY p95_hours DESC NULLS LAST, carrier LIMIT 1
        """,
        "columns": ["carrier", "deliveries", "p95_hours"],
        "expected": ["Собственная доставка", 3733, "23.15"],
    },
    {
        "id": "expert_05_purchase_as_of",
        "description": "Согласованные и неоплаченные на конец 31 марта 2025",
        "sql": """
            WITH s AS (
              SELECT "Подразделение" department,
                     replace(NULLIF("Согласованная сумма, руб.", ''), ',', '.')::numeric amount,
                     NULLIF("Согласована (МСК)", '')::timestamp approved,
                     NULLIF("Оплачена (МСК)", '')::timestamp paid,
                     NULLIF("Отменена (МСК)", '')::timestamp cancelled,
                     NULLIF("Отклонена (МСК)", '')::timestamp rejected
              FROM raw."data_07_Заявки_на_закупку"
            )
            SELECT department, COUNT(*) n, ROUND(SUM(amount), 2) amount
            FROM s
            WHERE approved < TIMESTAMP '2025-04-01'
              AND (paid IS NULL OR paid >= TIMESTAMP '2025-04-01')
              AND (cancelled IS NULL OR cancelled >= TIMESTAMP '2025-04-01')
              AND (rejected IS NULL OR rejected >= TIMESTAMP '2025-04-01')
            GROUP BY department
            ORDER BY amount DESC NULLS LAST, department LIMIT 1
        """,
        "columns": ["department", "requests", "amount"],
        "expected": ["Информационные технологии", 4158, "851088269.49"],
    },
    {
        "id": "expert_06_retry_chains",
        "description": "Цепочки с успешным завершением после повторных попыток",
        "sql": """
            WITH a AS (
              SELECT "Задание" job,
                     "Код цепочки запусков" chain,
                     "Номер попытки"::int attempt, "Статус" status
              FROM raw."data_08_Фоновые_задания"
            ), c AS (
              SELECT job, chain, MAX(attempt) attempts,
                     (ARRAY_AGG(status ORDER BY attempt DESC))[1] final_status
              FROM a GROUP BY job, chain
            )
            SELECT job, COUNT(*) total_chains,
                   COUNT(*) FILTER (WHERE final_status = 'Успешно'
                     AND attempts > 1) retry_success,
                   ROUND(100.0 * COUNT(*) FILTER (WHERE final_status = 'Успешно'
                     AND attempts > 1) / NULLIF(COUNT(*), 0), 2) share_pct,
                   ROUND(AVG(attempts) FILTER (WHERE final_status = 'Успешно'
                     AND attempts > 1), 2) avg_attempts
            FROM c GROUP BY job
            ORDER BY share_pct DESC NULLS LAST, job LIMIT 1
        """,
        "columns": ["job", "total_chains", "retry_success", "share_pct", "avg_attempts"],
        "expected": ["Очистка временных файлов", 11083, 581, "5.24", "2.32"],
    },
]


def _normalized(value: Any) -> Any:
    if isinstance(value, (Decimal, int, float)) and not isinstance(value, bool):
        return Decimal(str(value))
    return value


def evaluate_row(actual_row: tuple | None, case: dict) -> dict:
    """Compare values exactly after SQL-side rounding, keeping their types."""
    expected = case["expected"]
    if actual_row is None:
        return {"status": "fail", "reason": "SQL не вернул строк", "expected": expected, "actual": None}
    if len(actual_row) != len(expected):
        return {
            "status": "fail", "reason": "Не совпадает число колонок",
            "expected": expected, "actual": [str(v) if v is not None else None for v in actual_row],
        }
    differences = []
    for name, actual, wanted in zip(case["columns"], actual_row, expected):
        # Expected decimal strings are converted for comparison, other strings stay text.
        expected_value = Decimal(wanted) if isinstance(wanted, str) and (
            wanted.startswith("-") or (wanted[:1].isdigit() and "." in wanted)
        ) else wanted
        if _normalized(actual) != _normalized(expected_value):
            differences.append({"column": name, "expected": str(wanted), "actual": str(actual)})
    return {
        "status": "pass" if not differences else "fail",
        "expected": expected,
        "actual": [str(v) if v is not None else None for v in actual_row],
        "differences": differences,
    }


def run_cases(conn, cases: list[dict]) -> dict:
    results = []
    for case in cases:
        try:
            with conn.transaction():
                conn.execute("SET TRANSACTION READ ONLY")
                conn.execute("SET LOCAL statement_timeout = '60000ms'")
                with conn.cursor() as cursor:
                    cursor.execute(case["sql"])
                    row = cursor.fetchone()
                    if row is not None and cursor.fetchone() is not None:
                        raise ValueError("Ожидалась ровно одна строка")
            checked = evaluate_row(row, case)
            results.append({
                "id": case["id"], "description": case["description"], **checked,
            })
        except (psycopg.Error, ValueError) as exc:
            results.append({
                "id": case["id"], "description": case["description"],
                "status": "error", "error": str(exc).splitlines()[0][:300],
            })
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset": "original nine raw CSV tables",
        "query_count": len(cases),
        "passed": sum(r["status"] == "pass" for r in results),
        "failed": sum(r["status"] == "fail" for r in results),
        "errors": sum(r["status"] == "error" for r in results),
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify expert reference calculations on PostgreSQL")
    parser.add_argument("--case", choices=[c["id"] for c in CASES], action="append",
                        help="Run selected cases; defaults to all six")
    parser.add_argument("--report", type=Path,
                        help="Write a local JSON report (do not commit dataset outputs)")
    args = parser.parse_args()
    chosen = [c for c in CASES if args.case is None or c["id"] in args.case]
    try:
        with connect_readonly() as conn:
            report = run_cases(conn, chosen)
        for result in report["results"]:
            print(f'{result["status"].upper():5} {result["id"]}')
            if result["status"] != "pass":
                print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
        print(f'Итого: {report["passed"]}/{report["query_count"]} OK; '
              f'{report["failed"]} расхождений; {report["errors"]} ошибок')
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                   encoding="utf-8")
        return 0 if report["passed"] == report["query_count"] else 1
    except (OSError, ValueError, psycopg.Error) as exc:
        print(f"Ошибка проверки: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
