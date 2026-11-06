"""Проверка вывода только подтверждённых периодов из каталога."""
import unittest

from .catalog_summary import date_range_section
from .describe import describe_data


def sample_catalog():
    return {
        "schema": "raw",
        "tables": [
            {
                "name": "data_01_Продажи",
                "columns": [
                    {
                        "name": "Дата операции",
                        "inferred_type": "date",
                        "conversion": {
                            "date_range": {
                                "min": "2025-02-01", "max": "2025-03-31",
                                "scope": "full_column_valid_values",
                            },
                            "quality": {
                                "total_rows": 5, "missing_rows": 1,
                                "invalid_rows": 1, "convertible_rows": 3,
                            },
                        },
                    }
                ],
            },
            {
                "name": "new_csv",
                "columns": [{
                    "name": "Дата отправки",
                    "inferred_type": "timestamp",
                    "conversion": {
                        "date_range": {
                            "min": None, "max": None,
                            "scope": "full_column_valid_values",
                        },
                        "quality": {
                            "total_rows": 2, "missing_rows": 2,
                            "invalid_rows": 0, "convertible_rows": 0,
                        },
                    },
                }],
            },
        ],
    }


class DateRangeDescriptionTests(unittest.TestCase):
    def test_range_contains_field_dates_and_quality(self):
        text = date_range_section(sample_catalog())
        self.assertIn("2025-02-01", text)
        self.assertIn("2025-03-31", text)
        self.assertIn("Дата операции", text)
        self.assertIn("пропусков: 1", text)
        self.assertIn("некорректных значений: 1", text)

    def test_empty_date_column_is_not_guessed(self):
        text = date_range_section(sample_catalog())
        self.assertIn("Дата отправки", text)
        self.assertIn("корректных дат нет", text)

    def test_missing_audit_is_disclosed(self):
        catalog = {"tables": [{
            "name": "new_csv",
            "columns": [{
                "name": "Событие", "inferred_type": "date",
                "conversion": {"policy": "unverified"},
            }],
        }]}
        self.assertIn("точный период пока не проверен", date_range_section(catalog))

    def test_no_date_columns_no_period_section(self):
        catalog = {"tables": [{
            "name": "source",
            "columns": [{"name": "ID", "inferred_type": "identifier"}],
        }]}
        self.assertEqual(date_range_section(catalog), "")

    def test_describe_data_includes_verified_period(self):
        class Client:
            def chat(self, messages, max_tokens):
                if "Тебе дали описание одной таблицы" in messages[0]["content"]:
                    return "Содержит продажи. Позволяет анализировать объёмы."
                return "Общая картина: продажа товаров.\nЧто можно спросить:\n1. Сколько заказов?"
        catalog = {"schema": "raw", "tables": [sample_catalog()["tables"][0]]}
        result = describe_data(catalog, client=Client())
        self.assertIn("2025-02-01", result["answer"])
        self.assertIn("2025-03-31", result["answer"])


if __name__ == "__main__":
    unittest.main()
