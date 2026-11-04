"""Unit checks for expert PostgreSQL reference runner without a database."""
import unittest
from decimal import Decimal
from unittest.mock import Mock

from expert_regression import (
    CASES, EXPERT_API_CASES, _as_decimal, evaluate_api_response,
    evaluate_row, run_api_cases, run_cases,
)


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
        cursor.fetchone.side_effect = [
            tuple(
                Decimal(v) if isinstance(v, str) and v[:1].isdigit() and "." in v else v
                for v in CASES[0]["expected"]
            ),
            None,
        ]
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


class ExpertApiRegressionTests(unittest.TestCase):
    def test_six_questions_match_reference_set(self):
        self.assertEqual({item["id"] for item in CASES}, set(EXPERT_API_CASES))
        self.assertTrue(all("question" in meta and "table" in meta
                            for meta in EXPERT_API_CASES.values()))

    def test_api_checks_all_values_in_one_row(self):
        case = CASES[0]
        got = {
            "table": EXPERT_API_CASES[case["id"]]["table"],
            "rows": [
                ["1019", "1,55", "2,02", "30,32%", "40"],
                ["1024", "1,00", "2,00", "26,46%", "40"],
            ],
        }
        report = evaluate_api_response(got, case)
        self.assertEqual(report["status"], "pass")

    def test_api_does_not_match_values_across_rows(self):
        case = CASES[0]
        got = {
            "table": EXPERT_API_CASES[case["id"]]["table"],
            "rows": [
                ["1019", "1,55", "2,02"],
                ["1024", "30,32%", "40"],
            ],
        }
        self.assertEqual(evaluate_api_response(got, case)["status"], "fail")

    def test_api_fails_on_wrong_table(self):
        case = CASES[4]
        got = {
            "table": "data_01_Продажи",
            "rows": [["Информационные технологии", 4158, "851088269,49"]],
        }
        self.assertEqual(evaluate_api_response(got, case)["status"], "fail")

    def test_api_no_sql_is_not_wrong_table(self):
        case = CASES[0]
        report = evaluate_api_response({
            "table": None, "sql": None, "rows": None,
            "answer": "Не получилось надёжно посчитать",
        }, case)
        self.assertEqual(report["diagnosis"], "no_sql")
        self.assertEqual(report["expected"], case["expected"])

    def test_api_empty_result_is_not_wrong_table(self):
        case = CASES[4]
        report = evaluate_api_response({
            "table": None,
            "sql": 'SELECT "Подразделение", SUM("Согласованная сумма, руб.") FROM t GROUP BY 1',
            "rows": None,
        }, case)
        self.assertEqual(report["diagnosis"], "empty_result")
        self.assertEqual(report["expected"], case["expected"])

    def test_api_purchase_full_department(self):
        case = CASES[4]
        got = {
            "table": EXPERT_API_CASES[case["id"]]["table"],
            "rows": [["Информационные технологии", 4158, "851 088 269,49 ₽"]],
        }
        self.assertEqual(evaluate_api_response(got, case)["status"], "pass")

    def test_api_rejects_wrong_rounded_values(self):
        case = CASES[2]
        got = {
            "table": EXPERT_API_CASES[case["id"]]["table"],
            "rows": [["Доставка заказов", 4515, "39,36"]],
        }
        self.assertEqual(evaluate_api_response(got, case)["status"], "fail")

    def test_api_empty_table_is_failure(self):
        case = CASES[3]
        got = {"table": EXPERT_API_CASES[case["id"]]["table"], "rows": []}
        self.assertEqual(evaluate_api_response(got, case)["status"], "fail")

    def test_nonfinite_and_invalid_values_are_not_numbers(self):
        for item in ("not a number", None, True, "NaN", float("inf")):
            with self.subTest(item=item):
                self.assertIsNone(_as_decimal(item))

    def test_api_runner_does_not_claim_model_success_unverified(self):
        case = CASES[5]
        meta = EXPERT_API_CASES[case["id"]]
        fake_asker = Mock(return_value={
            "table": meta["table"],
            "rows": [["Очистка временных файлов", 11083, 581, "5,24%", "2,32"]],
            "answer": "Проверяемый ответ", "sql": "SELECT ...",
        })
        report = run_api_cases(fake_asker, [case])
        self.assertEqual(report["passed"], 1)
        self.assertEqual(report["errors"], 0)
        fake_asker.assert_called_once_with([meta["question"]], "expert-" + case["id"])

    def test_api_runner_captures_errors(self):
        failed_asker = Mock(side_effect=RuntimeError("API unavailable"))
        report = run_api_cases(failed_asker, CASES[:1])
        self.assertEqual(report["errors"], 1)
        self.assertEqual(report["passed"], 0)


if __name__ == "__main__":
    unittest.main()
