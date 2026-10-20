"""Проверки безопасного именования таблиц и колонок без запущенной БД."""

import unittest
from pathlib import Path

from load_csv import columns_for, fit_identifier, table_name_for


class NamingTests(unittest.TestCase):
    def test_russian_file_name(self):
        self.assertEqual(table_name_for(Path("01_Продажи.csv")), "data_01_Продажи")

    def test_duplicate_and_empty_columns(self):
        self.assertEqual(
            columns_for(["user_id", "user_id", "", "price"]),
            ["user_id", "user_id__2", "column_3", "price"],
        )

    def test_identifier_byte_limit(self):
        result = fit_identifier("название_" * 20)
        self.assertLessEqual(len(result.encode("utf-8")), 63)

    def test_truncated_column_names_remain_unique(self):
        names = columns_for(["длинная_колонка" * 10, "длинная_колонка" * 10])
        self.assertNotEqual(names[0], names[1])
        self.assertTrue(all(len(name.encode("utf-8")) <= 63 for name in names))


if __name__ == "__main__":
    unittest.main()
