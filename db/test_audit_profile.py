"""Проверки автоматического аудита профиля без подключения к PostgreSQL."""
import unittest
from unittest.mock import Mock, patch

from audit_profile import audit_profile, collect_candidates


PROFILE = {
    "schema": "raw",
    "generated_at_utc": "2026-10-21T12:00:00+00:00",
    "tables": [
        {"name": "data_01_Продажи", "columns": [
            {"name": "Цена", "inferred_type": "decimal", "confidence": 1.0},
            {"name": "Дата", "inferred_type": "timestamp", "confidence": 1.0},
            {"name": "Код заказа", "inferred_type": "identifier", "confidence": 1.0},
            {"name": "Сомнительное", "inferred_type": "mixed", "confidence": 0.6},
        ]},
        {"name": "data_06_Рекламные_кампании", "columns": [
            {"name": "Дата", "inferred_type": "date", "confidence": 1.0}
        ]},
    ],
}


class AuditProfileTests(unittest.TestCase):
    def test_only_supported_casts(self):
        selected = collect_candidates(PROFILE)
        self.assertEqual([c["kind"] for c in selected], ["decimal", "timestamp", "date"])
        self.assertNotIn("Код заказа", [c["column"] for c in selected])

    def test_filter_and_limit(self):
        selected = collect_candidates(PROFILE, "data_01_Продажи")
        self.assertEqual(len(selected), 2)
        self.assertEqual(len(audit_profile(Mock(), PROFILE, limit=1)["columns"]), 1)

    def test_reject_bad_schema_or_missing_table(self):
        with self.assertRaises(ValueError):
            collect_candidates({**PROFILE, "schema": "pg_catalog"})
        with self.assertRaises(ValueError):
            collect_candidates(PROFILE, "unknown")

    def test_reject_nonpositive_limit(self):
        with self.assertRaises(ValueError):
            audit_profile(Mock(), PROFILE, limit=0)

    @patch("audit_profile.audit_column")
    def test_counts_bad_and_missing_values(self, audit):
        audit.side_effect = [
            {"total_rows": 3, "missing_rows": 0, "invalid_rows": 0,
             "convertible_rows": 3, "source_modified": False},
            {"total_rows": 3, "missing_rows": 2, "invalid_rows": 0,
             "convertible_rows": 1, "source_modified": False},
            {"total_rows": 3, "missing_rows": 0, "invalid_rows": 1,
             "convertible_rows": 2, "source_modified": False},
        ]
        report = audit_profile(Mock(), PROFILE)
        s = report["summary"]
        self.assertEqual(s["candidate_columns"], 3)
        self.assertEqual(s["checked_columns"], 3)
        self.assertEqual(s["columns_with_missing_values"], 1)
        self.assertEqual(s["columns_with_invalid_values"], 1)
        self.assertEqual(s["fully_convertible_columns"], 1)
        self.assertFalse(report["source_modified"])

    @patch("audit_profile.audit_column")
    def test_column_error_does_not_lose_other_results(self, audit):
        import psycopg
        audit.side_effect = [
            psycopg.errors.QueryCanceled("cancelled"),
            {"total_rows": 2, "missing_rows": 0, "invalid_rows": 0,
             "convertible_rows": 2, "source_modified": False},
        ]
        report = audit_profile(Mock(), PROFILE, limit=2)
        self.assertEqual(report["summary"]["failed_columns"], 1)
        self.assertEqual(report["summary"]["checked_columns"], 1)
        self.assertEqual(report["columns"][0]["status"], "error")
        self.assertEqual(report["columns"][1]["status"], "checked")


if __name__ == "__main__":
    unittest.main()
