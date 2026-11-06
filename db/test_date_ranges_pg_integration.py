"""Real PostgreSQL 16 smoke for verified date MIN/MAX (issue #119).

Only runs against the dedicated ephemeral CI database; never against local raw.
"""
import os
import unittest

import psycopg

from db.safe_cast import audit_column


@unittest.skipUnless(os.getenv("CI_DATE_AUDIT_DSN"), "only dedicated PostgreSQL CI service")
class DateAuditPostgresIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dsn = os.environ["CI_DATE_AUDIT_DSN"]
        cls.conn = psycopg.connect(dsn, autocommit=True, connect_timeout=4)
        if cls.conn.info.dbname != "bi_ci_date_audit":
            cls.conn.close()
            raise RuntimeError("Refusing to create tables outside the isolated bi_ci_date_audit DB")
        cls.conn.execute("CREATE TABLE public.ci_date_audit_fixture "
                         "(day_text text, timestamp_text text, numeric_text text)")
        with cls.conn.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO public.ci_date_audit_fixture VALUES (%s, %s, %s)",
                [
                    ("2025-02-01", "2025-02-01 09:30:00", "10"),
                    ("2025-03-31", "2025-03-31 23:59:00", "12"),
                    ("", "", "bad"),
                    ("2025-02-30", "2025-02-30 07:00:00", "0"),
                ],
            )

    @classmethod
    def tearDownClass(cls):
        # Safe only because setUpClass insists on a dedicated CI database.
        cls.conn.execute("DROP TABLE IF EXISTS public.ci_date_audit_fixture")
        cls.conn.close()

    def test_date_bounds_and_full_column_quality_are_readonly(self):
        with self.conn.transaction():
            self.conn.execute("SET TRANSACTION READ ONLY")
            result = audit_column(self.conn, "public", "ci_date_audit_fixture",
                                  "day_text", "date")
        self.assertEqual(
            [result[k] for k in ("total_rows", "missing_rows", "invalid_rows",
                                 "convertible_rows")],
            [4, 1, 1, 2],
        )
        self.assertEqual(result["date_range"], {
            "min": "2025-02-01",
            "max": "2025-03-31",
            "scope": "full_column_valid_values",
        })
        self.assertFalse(result["source_modified"])

    def test_timestamp_preserves_time_of_day(self):
        with self.conn.transaction():
            self.conn.execute("SET TRANSACTION READ ONLY")
            result = audit_column(self.conn, "public", "ci_date_audit_fixture",
                                  "timestamp_text", "timestamp")
        self.assertEqual(result["date_range"], {
            "min": "2025-02-01T09:30:00",
            "max": "2025-03-31T23:59:00",
            "scope": "full_column_valid_values",
        })

    def test_batch_date_ranges_matches_individual_pg16_audit(self):
        from backend.llm.sql_runner import PostgresRunner, clear_values_cache

        clear_values_cache()
        table = {"name": "ci_date_audit_fixture", "columns": [
            {"name": "day_text", "inferred_type": "date",
             "conversion": {"target_type": "date", "policy": "safe_with_nulls"}},
            {"name": "timestamp_text", "inferred_type": "timestamp",
             "conversion": {"target_type": "timestamp", "policy": "safe_with_nulls"}},
            {"name": "numeric_text", "inferred_type": "decimal",
             "conversion": {"target_type": "decimal", "policy": "safe_with_nulls"}},
        ]}
        runner = PostgresRunner(self.conn, schema="public", source="ci-date-batch")
        ranges = runner.date_ranges(table, ["day_text", "timestamp_text"])
        self.assertEqual(tuple(v.isoformat() for v in ranges["day_text"]),
                         ("2025-02-01", "2025-03-31"))
        self.assertEqual(tuple(v.isoformat() for v in ranges["timestamp_text"]),
                         ("2025-02-01T09:30:00", "2025-03-31T23:59:00"))
        self.assertEqual(runner.date_range(table, "day_text"), ranges["day_text"])

    def test_raw_data_unchanged_after_audit(self):
        rows = self.conn.execute("SELECT count(*) FROM public.ci_date_audit_fixture").fetchone()[0]
        self.assertEqual(rows, 4)


if __name__ == "__main__":
    unittest.main()
