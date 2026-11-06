"""PostgreSQL 16 execution check for the calendar-normalized SQL calculator.

Runs only when PG_SQL_SMOKE_DSN is configured; test data are synthetic.
No access to organizers' original CSV or local databases is required.
"""
import os
import unittest
from datetime import date
from decimal import Decimal

import psycopg

from normalized_metrics import comparison_sql


@unittest.skipUnless(os.getenv("PG_SQL_SMOKE_DSN"), "PostgreSQL smoke DSN not configured")
class CalendarNormalizedPostgresSmoke(unittest.TestCase):
    def test_full_calendar_denominator_and_invalid_values(self):
        dsn = os.environ["PG_SQL_SMOKE_DSN"]
        with psycopg.connect(dsn) as setup:
            setup.execute("CREATE SCHEMA IF NOT EXISTS qa_calendar")
            setup.execute('DROP TABLE IF EXISTS qa_calendar."orders"')
            setup.execute(
                'CREATE TABLE qa_calendar."orders" '
                '("Препарат" text, "Аптека" text, "Дата" text, "Количество" text)'
            )
            rows = [
                ("1019", "P1", "2025-02-01", "28"),
                ("1019", "P2", "2025-02-15", "28"),
                ("1019", "P1", "2025-03-01", "31"),
                ("1019", "P2", "2025-03-20", "62"),
                ("1019", "P1", "2025-03-21", "not-a-number"),
                ("2020", "P1", "2025-03-25", "10"),
                # This third pharmacy is counted despite having no Feb/Mar rows.
                ("2020", "P3", "2024-12-01", "100"),
            ]
            with setup.cursor() as cursor:
                cursor.executemany(
                    'INSERT INTO qa_calendar."orders" VALUES (%s, %s, %s, %s)',
                    rows,
                )
        query, args = comparison_sql(
            schema="qa_calendar",
            table="orders",
            group_column="Препарат",
            entity_column="Аптека",
            date_column="Дата",
            quantity_column="Количество",
            first_start=date(2025, 2, 1),
            first_end=date(2025, 3, 1),
            second_start=date(2025, 3, 1),
            second_end=date(2025, 4, 1),
        )
        with psycopg.connect(dsn) as connection:
            with connection.transaction():
                connection.execute("SET TRANSACTION READ ONLY")
                with connection.cursor() as cursor:
                    cursor.execute(query, args)
                    actual = cursor.fetchall()
        by_drug = {row[0]: row for row in actual}
        self.assertEqual(set(by_drug), {"1019", "2020"})
        result = by_drug["1019"]
        self.assertEqual(result[1:4], (3, Decimal("56"), Decimal("93")))
        self.assertEqual(result[4:7], (
            Decimal("0.6667"), Decimal("1.0000"), Decimal("50.00")
        ))
        self.assertEqual(by_drug["2020"][2], Decimal("0"))
        self.assertIsNone(by_drug["2020"][6])


if __name__ == "__main__":
    unittest.main()
