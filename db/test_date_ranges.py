"""Проверки точных диапазонов дат: SQL, аудит и контракт каталога."""
import unittest
from datetime import date, datetime
from unittest.mock import Mock

from catalog_db import build_catalog
from safe_cast import audit_column, quality_query


def data(kind="date", date_range=None, *, convertible=3, missing=1, invalid=0):
    profile = {
        "schema": "raw", "generated_at_utc": "t1",
        "tables": [{"name": "orders", "columns": [{
            "name": "Создано", "inferred_type": kind,
        }]}],
    }
    item = {
        "table": "orders", "column": "Создано", "target_type": kind,
        "status": "checked", "total_rows": convertible + missing + invalid,
        "convertible_rows": convertible, "missing_rows": missing,
        "invalid_rows": invalid,
    }
    if date_range is not None:
        item["date_range"] = date_range
    audit = {
        "schema": "raw", "source_profile_generated_at_utc": "t1",
        "statistics_are_full_column": True, "columns": [item],
    }
    return profile, audit


class DateRangesTests(unittest.TestCase):
    def test_date_sql_has_min_and_max(self):
        statement = quality_query("raw", "orders", "Создано", "date").as_string()
        self.assertIn("MIN(", statement)
        self.assertIn("MAX(", statement)
        self.assertIn("first_valid_date", statement)
        self.assertIn("last_valid_date", statement)
        self.assertIn("pg_input_is_valid(", statement)
        self.assertIn('"raw"."orders"', statement)

    def test_timestamp_sql_has_bounds(self):
        statement = quality_query("raw", "orders", "Создано", "timestamp").as_string()
        self.assertIn("first_valid_date", statement)
        self.assertIn("timestamp without time zone", statement)

    def test_numeric_quality_query_remains_unchanged(self):
        statement = quality_query("raw", "orders", "Сумма", "decimal").as_string()
        self.assertNotIn("first_valid_date", statement)
        self.assertNotIn("last_valid_date", statement)

    def test_audit_date_boundaries_serialized(self):
        conn = Mock()
        conn.execute.side_effect = [
            Mock(fetchone=Mock(return_value=(1,))),
            Mock(fetchone=Mock(return_value=(
                4, 1, 0, 3, date(2025, 2, 1), date(2025, 3, 31)
            ))),
        ]
        result = audit_column(conn, "raw", "orders", "Создано", "date")
        self.assertEqual(result["date_range"], {
            "min": "2025-02-01", "max": "2025-03-31",
            "scope": "full_column_valid_values",
        })
        self.assertEqual(result["missing_rows"], 1)

    def test_timestamp_retains_time(self):
        conn = Mock()
        conn.execute.side_effect = [
            Mock(fetchone=Mock(return_value=(1,))),
            Mock(fetchone=Mock(return_value=(
                2, 0, 0, 2,
                datetime(2025, 2, 1, 9, 30),
                datetime(2025, 3, 31, 23, 59),
            ))),
        ]
        result = audit_column(conn, "raw", "orders", "Создано", "timestamp")
        self.assertEqual(result["date_range"]["max"], "2025-03-31T23:59:00")

    def test_all_missing_has_null_bounds(self):
        conn = Mock()
        conn.execute.side_effect = [
            Mock(fetchone=Mock(return_value=(1,))),
            Mock(fetchone=Mock(return_value=(2, 2, 0, 0, None, None))),
        ]
        result = audit_column(conn, "raw", "orders", "Создано", "date")
        self.assertEqual(result["date_range"]["min"], None)
        self.assertEqual(result["date_range"]["max"], None)

    def test_catalog_passes_verified_range(self):
        ranges = {"min": "2025-02-01", "max": "2025-03-31",
                  "scope": "full_column_valid_values"}
        p, a = data(date_range=ranges)
        col = build_catalog(p, a)["tables"][0]["columns"][0]
        self.assertEqual(col["conversion"]["date_range"], ranges)

    def test_legacy_audit_without_range_is_supported(self):
        p, a = data()
        col = build_catalog(p, a)["tables"][0]["columns"][0]
        self.assertNotIn("date_range", col["conversion"])

    def test_missing_only_range_is_allowed(self):
        p, a = data(
            date_range={"min": None, "max": None,
                        "scope": "full_column_valid_values"},
            convertible=0, missing=4,
        )
        col = build_catalog(p, a)["tables"][0]["columns"][0]
        self.assertEqual(col["conversion"]["date_range"]["min"], None)

    def test_incorrect_boundaries_are_rejected(self):
        samples = [
            {"min": "2025-03-31", "max": "2025-02-01",
             "scope": "full_column_valid_values"},
            {"min": None, "max": "2025-03-31",
             "scope": "full_column_valid_values"},
            {"min": "2025-02-01", "max": "2025-03-31",
             "scope": "sample_only"},
            "2025-02-01",
        ]
        for value in samples:
            with self.subTest(value=value):
                p, a = data(date_range=value)
                with self.assertRaises(ValueError):
                    build_catalog(p, a)

    def test_nonempty_dates_without_convertible_rows_are_rejected(self):
        p, a = data(
            convertible=0, missing=2,
            date_range={"min": "2025-01-01", "max": "2025-01-01",
                        "scope": "full_column_valid_values"},
        )
        with self.assertRaises(ValueError):
            build_catalog(p, a)


if __name__ == "__main__":
    unittest.main()
