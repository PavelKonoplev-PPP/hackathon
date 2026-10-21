"""Проверки универсального безопасного SQL-приведения без запущенной БД."""

import unittest
from unittest.mock import Mock

from safe_cast import audit_column, cast_parts, quality_query, safe_cast_expression


class SafeCastTests(unittest.TestCase):
    def test_decimal_comma_and_numeric_validation(self):
        s = safe_cast_expression("Цена, руб.", "decimal").as_string()
        self.assertIn('replace(NULLIF(btrim("Цена, руб."::text), \'\'), \',\', \'.\')', s)
        self.assertIn("pg_input_is_valid(", s)
        self.assertIn("::numeric", s)
        self.assertIn("ELSE NULL::numeric", s)
        self.assertIn("^[+-]?", s)

    def test_integer_overflow_is_checked_before_cast(self):
        s = safe_cast_expression("Количество", "integer").as_string()
        self.assertIn("pg_input_is_valid(", s)
        self.assertIn("'bigint'", s)
        self.assertIn("::bigint", s)

    def test_russian_date_normalization(self):
        s = safe_cast_expression("Дата", "date").as_string()
        self.assertIn("substr(", s)
        self.assertIn("7, 4", s)
        self.assertIn("pg_input_is_valid(", s)
        self.assertIn("::date", s)

    def test_timestamp_without_time_zone(self):
        s = safe_cast_expression("Дата операции (МСК)", "timestamp").as_string()
        self.assertIn("'timestamp without time zone'", s)
        self.assertIn("::timestamp without time zone", s)

    def test_identifier_is_never_auto_converted(self):
        for kind in ("identifier", "text", "category", "mixed", "unknown", "numeric; DROP TABLE x"):
            with self.assertRaises(ValueError):
                safe_cast_expression("Код обращения", kind)

    def test_quoted_identifiers_prevent_sql_injection(self):
        s = quality_query("raw", 'sales"; DROP TABLE z; --',
                          'Цена"; DELETE FROM raw.t; --', "decimal").as_string()
        self.assertIn('"raw"."sales""; DROP TABLE z; --"', s)
        self.assertIn('"Цена""; DELETE FROM raw.t; --"', s)

    def test_quality_reports_every_row(self):
        s = quality_query("raw", "data_01_Продажи", "Цена", "decimal").as_string()
        self.assertIn("missing_rows", s)
        self.assertIn("invalid_rows", s)
        self.assertIn("convertible_rows", s)
        self.assertIn('FROM "raw"."data_01_Продажи"', s)

    def test_empty_column_rejected(self):
        with self.assertRaises(ValueError):
            cast_parts("", "decimal")

    def test_audit_rejects_missing_column(self):
        conn = Mock()
        conn.execute.return_value.fetchone.return_value = None
        with self.assertRaises(ValueError):
            audit_column(conn, "raw", "sales", "no_such_col", "decimal")

    def test_audit_reports_counts_without_unsafe_values(self):
        conn = Mock()
        conn.execute.side_effect = [
            Mock(fetchone=Mock(return_value=(1,))),
            Mock(fetchone=Mock(return_value=(4, 1, 1, 2))),
        ]
        out = audit_column(conn, "raw", "sales", "price", "decimal")
        self.assertEqual(out["missing_rows"], 1)
        self.assertEqual(out["invalid_rows"], 1)
        self.assertEqual(out["convertible_rows"], 2)
        self.assertFalse(out["source_modified"])


if __name__ == "__main__":
    unittest.main()
