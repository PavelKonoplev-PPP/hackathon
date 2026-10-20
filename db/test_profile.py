"""Тесты профилирования: универсальные типы и структура JSON."""

import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import Mock

from profile_db import analyze_column, profile_database


class TypeInferenceTests(unittest.TestCase):
    def test_decimal_comma_and_empty_values(self):
        p = analyze_column(
            "Цена за единицу", "text", ["298,70", "2255,70", "", None, "1490,00"]
        )
        self.assertEqual(p["inferred_type"], "decimal")
        self.assertEqual(p["numeric_format"], "decimal_comma")
        self.assertEqual(p["sample_null_count"], 1)
        self.assertEqual(p["sample_blank_count"], 1)
        self.assertEqual(p["sample_min"], "298.70")
        self.assertEqual(p["sample_max"], "2255.70")

    def test_integer_including_negative_returns(self):
        p = analyze_column("Количество, шт.", "text", ["9", "-6", "1"])
        self.assertEqual(p["inferred_type"], "integer")
        self.assertEqual(p["sample_min"], "-6")
        self.assertEqual(p["sample_max"], "9")

    def test_iso_timestamp(self):
        p = analyze_column("Дата операции (МСК)", "text",
                           ["2024-10-01 02:27:58", "2024-10-02 04:00:00"])
        self.assertEqual(p["inferred_type"], "timestamp")

    def test_date_and_postgres_native(self):
        self.assertEqual(analyze_column("Дата", "text",
                                        ["2024-10-01"])["inferred_type"], "date")
        self.assertEqual(analyze_column("Дата", "date",
                                        [date(2024, 10, 1)])["inferred_type"], "date")
        self.assertEqual(analyze_column("Сумма", "numeric",
                                        [Decimal("22.5")])["inferred_type"], "decimal")

    def test_identifier_and_leading_zeros(self):
        self.assertEqual(analyze_column("Код позиции", "text",
                                        ["ПР-ПЗ-01", "ПР-ПЗ-02"])["inferred_type"],
                         "identifier")
        self.assertEqual(analyze_column("ID_Аптеки", "text",
                                        ["0001", "0002"])["inferred_type"],
                         "identifier")
        self.assertEqual(analyze_column("Случайное поле", "text",
                                        ["0012", "0077"])["inferred_type"],
                         "identifier")

    def test_category_and_mixed(self):
        values = ["Мобильное приложение", "Интернет-магазин"] * 20
        self.assertEqual(analyze_column("Канал продажи", "text",
                                        values)["inferred_type"], "category")
        p = analyze_column("Значение", "text", ["12", "20", "нет", "ошибка"])
        self.assertEqual(p["inferred_type"], "mixed")
        self.assertIsNotNone(p["warning"])

    def test_no_examples(self):
        p = analyze_column("Товар", "text", ["A", "B"], include_examples=False)
        self.assertEqual(p["examples"], [])

    def test_empty_column(self):
        p = analyze_column("Поле", "text", [None, "", " "])
        self.assertEqual(p["inferred_type"], "unknown")
        self.assertEqual(p["sample_blank_count"], 2)

    def test_truncate_examples(self):
        p = analyze_column("Название", "text", ["a" * 500 + "\n"])
        self.assertLessEqual(len(p["examples"][0]), 80)


class DatabaseProfilingTests(unittest.TestCase):
    def test_profile_uses_metadata_and_limited_sample(self):
        conn = Mock()
        conn.execute.side_effect = [
            Mock(fetchall=Mock(return_value=[("sales", 1000)])),
            Mock(fetchall=Mock(return_value=[
                ("Код", "text", "NO"), ("Цена", "text", "YES")
            ])),
            Mock(fetchall=Mock(return_value=[
                ("A-1", "298,70"), ("A-2", "399,90")
            ])),
        ]
        result = profile_database(conn, schema="raw", sample_size=2)
        self.assertEqual(len(result["tables"]), 1)
        table = result["tables"][0]
        self.assertEqual(table["sampled_rows"], 2)
        self.assertEqual(table["estimated_total_rows"], 1000)
        self.assertEqual(table["columns"][1]["inferred_type"], "decimal")
        self.assertTrue(result["sampling"]["statistics_are_sample_only"])
        sql_query = conn.execute.call_args_list[2].args[0]
        self.assertEqual(sql_query.as_string(), 'SELECT * FROM "raw"."sales" LIMIT %s')
        self.assertEqual(conn.execute.call_args_list[2].args[1], (2,))

    def test_reject_system_schema_and_oversized_sample(self):
        with self.assertRaises(ValueError):
            profile_database(Mock(), schema="pg_catalog")
        with self.assertRaises(ValueError):
            profile_database(Mock(), sample_size=1001)

    def test_report_of_empty_schema(self):
        conn = Mock()
        conn.execute.return_value.fetchall.return_value = []
        result = profile_database(conn, schema="test")
        self.assertEqual(result["tables"], [])


if __name__ == "__main__":
    unittest.main()
