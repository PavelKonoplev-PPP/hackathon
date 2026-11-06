"""Execute segment/outlier SQL against isolated PostgreSQL 16 test data.

Only runs when PG_SQL_SMOKE_DSN is provided by CI. Not an E2E check of the agent.
"""
import os
import unittest
from datetime import date
from decimal import Decimal

import psycopg

from segment_quality import outlier_sql, segment_comparison_sql


@unittest.skipUnless(os.getenv("PG_SQL_SMOKE_DSN"), "SQL smoke database unavailable")
class SegmentPostgresSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dsn = os.environ["PG_SQL_SMOKE_DSN"]
        with psycopg.connect(cls.dsn) as conn:
            conn.execute("CREATE SCHEMA IF NOT EXISTS qa_segments")
            conn.execute('DROP TABLE IF EXISTS qa_segments."sales"')
            conn.execute('DROP TABLE IF EXISTS qa_segments."delivery"')
            conn.execute(
                'CREATE TABLE qa_segments."sales" '
                '("Канал" text, "Регион" text, "Стоимость" text)'
            )
            conn.execute(
                'CREATE TABLE qa_segments."delivery" '
                '("Способ доставки" text, "Перевозчик" text, '
                '"Стоимость доставки, руб." text, "Дата отправки" text)'
            )
            sample = [( "A", "X", str(v)) for v in (1, 2, 2, 3, 4, 4, 5, 100)]
            sample += [("A", "X", "not-numeric"), ("B", "Y", "5"), ("B", "Y", "15")]
            deliveries = [
                ("Курьер", "Свой", str(v), f"2025-02-{i+1:02d}")
                for i, v in enumerate((10, 20, 30, 40))
            ] + [
                ("Курьер", "Свой", str(v), f"2025-03-{i+1:02d}")
                for i, v in enumerate((20, 30, 40, 50))
            ] + [("Курьер", "Свой", "bad", "2025-03-15")]
            with conn.cursor() as cur:
                cur.executemany('INSERT INTO qa_segments."sales" VALUES (%s, %s, %s)', sample)
                cur.executemany(
                    'INSERT INTO qa_segments."delivery" VALUES (%s, %s, %s, %s)',
                    deliveries,
                )

    def run_readonly(self, statement, params):
        with psycopg.connect(self.dsn) as conn:
            with conn.transaction():
                conn.execute("SET TRANSACTION READ ONLY")
                with conn.cursor() as cur:
                    cur.execute(statement, params)
                    columns = [col.name for col in cur.description]
                    return columns, cur.fetchall()

    def test_outlier_fences_and_invalid_numeric(self):
        statement, args = outlier_sql(
            schema="qa_segments", table="sales",
            segments=["Канал", "Регион"], metric="Стоимость",
            min_sample=8, limit=10,
        )
        columns, rows = self.run_readonly(statement, args)
        self.assertIn("Выбросов", columns)
        mapped = {(row[0], row[1]): row for row in rows}
        a = mapped[("A", "X")]
        self.assertEqual(a[2:5], (9, 8, 1))
        self.assertEqual(a[7], 1)
        self.assertEqual(a[8:10], (Decimal("100"), Decimal("100")))
        self.assertEqual(a[10], "checked")
        b = mapped[("B", "Y")]
        self.assertIsNone(b[7])
        self.assertEqual(b[10], "insufficient_sample")

    def test_period_metrics_and_percent_alias(self):
        statement, args = segment_comparison_sql(
            schema="qa_segments", table="delivery",
            segments=["Способ доставки", "Перевозчик"],
            metric="Стоимость доставки, руб.", date_column="Дата отправки",
            first_start=date(2025, 2, 1), first_end=date(2025, 3, 1),
            second_start=date(2025, 3, 1), second_end=date(2025, 4, 1),
            min_sample=4,
        )
        columns, rows = self.run_readonly(statement, args)
        self.assertEqual(len(rows), 1)
        self.assertIn("Изменение, %", columns)
        row = dict(zip(columns, rows[0]))
        self.assertEqual(row["Записей 1"], 4)
        self.assertEqual(row["Записей 2"], 5)
        self.assertEqual(row["Числовых 2"], 4)
        self.assertEqual(row["Пропусков или ошибок 2"], 1)
        self.assertEqual(row["Среднее 1"], Decimal("25.0000"))
        self.assertEqual(row["Среднее 2"], Decimal("35.0000"))
        self.assertEqual(row["Изменение"], Decimal("10.0000"))
        self.assertEqual(row["Изменение, %"], Decimal("40.00"))


if __name__ == "__main__":
    unittest.main()
