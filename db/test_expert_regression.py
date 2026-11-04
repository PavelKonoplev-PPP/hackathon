"""Unit checks for expert PostgreSQL reference runner without a database."""
import unittest
from decimal import Decimal
from unittest.mock import Mock

from expert_regression import CASES, evaluate_row, run_cases


class ExpertRegressionTests(unittest.TestCase):
    def test_six_distinct_cases(self):
        self.assertEqual(len(CASES), 6)
        self.assertEqual(len({c["id"] for c in CASES}), 6)
        for case in CASES:
            self.assertIn("SELECT", case["sql"].upper())
            self.assertTrue(case["sql"].strip().upper().startswith(("WITH", "SELECT")))
            self.assertEqual(len(case["columns"]), len(case["expected"]))

    def test_expected_values_pass(self):
        for case in CASES:
            with self.subTest(case=case["id"]):
                row = tuple(
                    Decimal(v) if isinstance(v, str) and v[:1].isdigit() and "." in v else v
                    for v in case["expected"]
                )
                self.assertEqual(evaluate_row(row, case)["status"], "pass")

    def test_numeric_rounding_and_actual_decimal(self):
        case = CASES[1]
        row = ("Калининград", Decimal("1627110.35"),
               Decimal("2640589.14"), Decimal("62.29"))
        self.assertEqual(evaluate_row(row, case)["status"], "pass")

    def test_different_result_is_reported_not_corrected(self):
        case = CASES[1]
        row = ("Калининград", Decimal("1627110.35"),
               Decimal("2640589.14"), Decimal("62.30"))
        result = evaluate_row(row, case)
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["differences"][0]["column"], "growth_pct")

    def test_no_result_is_failure(self):
        self.assertEqual(evaluate_row(None, CASES[0])["status"], "fail")

    def test_unexpected_columns_are_failure(self):
        self.assertEqual(evaluate_row(("wrong",), CASES[0])["status"], "fail")

    def test_runner_readonly_and_time_bounded(self):
        cursor = Mock()
        cursor.fetchone.side_effect = [tuple(CASES[0]["expected"]), None]
        conn = Mock()
        conn.transaction.return_value.__enter__ = Mock()
        conn.transaction.return_value.__exit__ = Mock(return_value=False)
        conn.cursor.return_value.__enter__ = Mock(return_value=cursor)
        conn.cursor.return_value.__exit__ = Mock(return_value=False)
        result = run_cases(conn, CASES[:1])
        self.assertEqual(result["passed"], 1)
        conn.execute.assert_any_call("SET TRANSACTION READ ONLY")
        conn.execute.assert_any_call("SET LOCAL statement_timeout = '60000ms'")

    def test_failed_comparison_does_not_report_pass(self):
        cursor = Mock()
        cursor.fetchone.side_effect = [("wrong", "0", "0", "0", 0), None]
        conn = Mock()
        conn.transaction.return_value.__enter__ = Mock()
        conn.transaction.return_value.__exit__ = Mock(return_value=False)
        conn.cursor.return_value.__enter__ = Mock(return_value=cursor)
        conn.cursor.return_value.__exit__ = Mock(return_value=False)
        report = run_cases(conn, CASES[:1])
        self.assertEqual(report["passed"], 0)
        self.assertEqual(report["failed"], 1)

    def test_query_definitions_are_read_only(self):
        for case in CASES:
            with self.subTest(case=case["id"]):
                sql_text = case["sql"].upper()
                for keyword in ("UPDATE ", "DELETE ", "INSERT ", "TRUNCATE ", "DROP "):
                    self.assertNotIn(keyword, sql_text)

    def test_report_has_every_case_id(self):
        self.assertEqual(
            {c["id"] for c in CASES},
            {
                "expert_01_drug_daily_growth",
                "expert_02_city_net_growth",
                "expert_03_support_p90",
                "expert_04_delivery_p95",
                "expert_05_purchase_as_of",
                "expert_06_retry_chains",
            },
        )


if __name__ == "__main__":
    unittest.main()
