"""Regression for one-query-per-table date ranges in description."""
import unittest
from datetime import date
from unittest.mock import Mock

from backend.llm.describe import period_section
from backend.llm.sql_runner import PostgresRunner, QueryResult, clear_values_cache


def fixture():
    return {
        "name": "data_03_Задачи",
        "columns": [
            {"name": "Создано", "inferred_type": "date",
             "conversion": {"target_type": "date", "policy": "safe_complete"}},
            {"name": "Срок", "inferred_type": "date",
             "conversion": {"target_type": "date", "policy": "safe_complete"}},
            {"name": "ID", "inferred_type": "identifier"},
        ],
    }


class DateBatchTests(unittest.TestCase):
    def setUp(self):
        clear_values_cache()

    def test_two_date_columns_one_readonly_query_and_cache(self):
        runner = PostgresRunner(conn=object(), source="date-batch-ci")
        runner.run = Mock(return_value=QueryResult(
            columns=["min1", "max1", "min2", "max2"],
            rows=[("2025-01-01", "2025-02-01", "2025-01-05", "2025-04-01")],
        ))
        values = runner.date_ranges(fixture(), ["Создано", "Срок"])
        self.assertEqual(values["Создано"], ("2025-01-01", "2025-02-01"))
        self.assertEqual(values["Срок"], ("2025-01-05", "2025-04-01"))
        runner.run.assert_called_once()
        sql = runner.run.call_args.args[1]
        self.assertEqual(sql.count("MIN("), 2)
        self.assertEqual(sql.count("MAX("), 2)
        self.assertEqual(runner.date_ranges(fixture(), ["Создано", "Срок"]), values)
        runner.run.assert_called_once()

    def test_rejects_identifier_as_date(self):
        runner = PostgresRunner(conn=object(), source="date-batch-ci")
        with self.assertRaisesRegex(ValueError, "verified date type"):
            runner.date_ranges(fixture(), ["ID"])

    def test_description_uses_one_batch_not_each_date(self):
        class Batch:
            calls = 0

            def date_ranges(self, table, columns):
                self.calls += 1
                self.columns = columns
                return {
                    "Создано": (date(2025, 1, 1), date(2025, 2, 1)),
                    "Срок": (date(2025, 1, 5), date(2025, 4, 1)),
                }

            def date_range(self, table, column):
                raise AssertionError("Should use the date_ranges batch")

        runner = Batch()
        answer = period_section({"tables": [fixture()]}, runner)
        self.assertEqual(runner.calls, 1)
        self.assertEqual(runner.columns, ["Создано", "Срок"])
        self.assertIn("«Создано» с 01.01.2025 по 01.02.2025", answer)
        self.assertIn("«Срок» с 05.01.2025 по 01.04.2025", answer)


if __name__ == "__main__":
    unittest.main()
