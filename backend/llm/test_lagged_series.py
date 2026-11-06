"""Deterministic QA checks for lead/lag correlation across calendar buckets."""
import math
import unittest

from backend.llm.lagged_series import analyze_lags, summarize_lags


def months(values):
    return [[f"2025-{i + 1:02d}", x, y] for i, (x, y) in enumerate(values)]


class LaggedSeriesTests(unittest.TestCase):
    def test_known_positive_two_month_lead(self):
        x = [3, 11, 5, 17, 2, 9, 20, 7, 15, 4, 12, 19]
        y = [None, None] + [2 * v + 1 for v in x[:-2]]
        result = analyze_lags(months(zip(x, y)), max_lag=3, min_pairs=6)
        self.assertEqual(result["best"]["lag"], 2)
        self.assertAlmostEqual(result["best"]["r"], 1.0, places=12)
        self.assertEqual(result["best"]["pairs"], 10)
        self.assertEqual(result["conclusion"], "calculated")
        self.assertIn("опережает", summarize_lags(result, "Реклама", "Продажи"))

    def test_known_negative_lag(self):
        x = [4, 19, 7, 23, 3, 11, 28, 6, 15, 2, 17, 9]
        y = [None, None] + [3 * v for v in x[:-2]]
        swapped = months(zip(y, x))
        result = analyze_lags(swapped, max_lag=3, min_pairs=6)
        self.assertEqual(result["best"]["lag"], -2)
        self.assertAlmostEqual(result["best"]["r"], 1.0, places=12)

    def test_missing_period_is_not_treated_as_adjacent(self):
        rows = [
            ["2025-01", 1, 10],
            ["2025-02", 2, None],
            ["2025-04", 3, 20],
            ["2025-05", 4, 30],
            ["2025-06", 5, 40],
        ]
        result = analyze_lags(rows, max_lag=1, min_pairs=3)
        positive = next(row for row in result["results"] if row["lag"] == 1)
        # Jan -> Feb is absent (Y null), Feb -> Mar absent, Apr -> May, May -> Jun.
        self.assertEqual(positive["pairs"], 2)
        self.assertEqual(positive["status"], "insufficient_pairs")

    def test_pairwise_missing_rows(self):
        rows = months([
            (1, 3), (2, None), (3, 7), (None, 9), (5, 11), (6, 13)
        ])
        result = analyze_lags(rows, max_lag=0, min_pairs=4)
        self.assertEqual(result["best"]["pairs"], 4)
        self.assertAlmostEqual(result["best"]["r"], 1.0)

    def test_constant_x_is_not_zero_correlation(self):
        rows = months([(1, 1), (1, 2), (1, 3), (1, 4), (1, 5)])
        result = analyze_lags(rows, max_lag=0)
        self.assertIsNone(result["best"])
        self.assertEqual(result["results"][0]["status"], "zero_variance")
        self.assertEqual(result["conclusion"], "insufficient_data")

    def test_constant_y_is_not_zero_correlation(self):
        rows = months([(1, 3), (2, 3), (3, 3), (4, 3)])
        result = analyze_lags(rows, max_lag=0)
        self.assertIsNone(result["best"])

    def test_short_series_is_explicitly_insufficient(self):
        result = analyze_lags(months([(1, 4), (2, 5), (3, 6)]),
                              max_lag=2, min_pairs=4)
        self.assertIsNone(result["best"])
        self.assertTrue(all(row["status"] == "insufficient_pairs" for row in result["results"]))
        self.assertIn("Недостаточно", summarize_lags(result))

    def test_irregular_records_sorted_by_calendar_key(self):
        values = [
            ["2025-04", 4, 8], ["2025-01", 1, 2],
            ["2025-03", 3, 6], ["2025-02", 2, 4],
        ]
        result = analyze_lags(values, max_lag=0, min_pairs=3)
        self.assertEqual(result["best"]["pairs"], 4)
        self.assertAlmostEqual(result["best"]["r"], 1.0)

    def test_duplicate_buckets_rejected(self):
        rows = [["2025-01-01", 1, 2], ["2025-01-20", 4, 5]]
        with self.assertRaisesRegex(ValueError, "Повторяющийся период"):
            analyze_lags(rows, grain="month")

    def test_daily_buckets(self):
        rows = [["2025-01-01", 2, 0], ["2025-01-02", 4, 2],
                ["2025-01-03", 6, 4], ["2025-01-04", 8, 6],
                ["2025-01-05", 10, 8]]
        result = analyze_lags(rows, grain="day", max_lag=1, min_pairs=4)
        self.assertEqual(result["grain"], "day")
        self.assertEqual(next(r for r in result["results"] if r["lag"] == 1)["pairs"], 4)

    def test_numeric_comma_nonfinite_and_empty(self):
        rows = months([("1,5", "3,0"), ("2,5", "5,0"),
                       ("nan", 10), ("", 12), ("4,5", "9,0"),
                       (math.inf, 6), ("5,5", "11,0")])
        result = analyze_lags(rows, max_lag=0, min_pairs=4)
        self.assertEqual(result["best"]["pairs"], 4)
        self.assertAlmostEqual(result["best"]["r"], 1.0)

    def test_ambiguous_lags(self):
        # Strict linear trends have the same r at every lag; do not claim
        # the unshifted result proves a unique lead indicator.
        result = analyze_lags(months([(i, 2*i) for i in range(1, 10)]),
                              max_lag=2, min_pairs=4)
        self.assertTrue(result["ambiguous"])
        self.assertEqual(result["conclusion"], "ambiguous")
        self.assertEqual(result["best"]["lag"], 0)
        self.assertIn("однозначный оптимальный лаг не установлен",
                      summarize_lags(result))

    def test_bad_parameters_rejected(self):
        for options in (
            {"grain": "week"}, {"max_lag": -1}, {"max_lag": 37},
            {"max_lag": True}, {"min_pairs": 2}, {"min_pairs": True},
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                analyze_lags([], **options)

    def test_bad_input_and_period_rejected(self):
        cases = [
            [[None, 1, 2]],
            [["2025-13", 1, 2]],
            [["2025-01", 1]],
            "not-a-list",
        ]
        for rows in cases:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                analyze_lags(rows)

    def test_no_causal_claim_in_summary(self):
        result = analyze_lags(months([(1, 2), (3, 5), (2, 1), (7, 4)]),
                              max_lag=0)
        self.assertIn("не доказывает", summarize_lags(result))


if __name__ == "__main__":
    unittest.main()
