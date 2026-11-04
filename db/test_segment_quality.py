"""Tests for read-only outlier and segmented period queries."""
import unittest
from datetime import date

from segment_quality import outlier_sql, segment_comparison_sql


def outlier(**overrides):
    options = dict(schema="raw", table="sales", segments=["Канал", "Регион"],
                   metric="Стоимость", min_sample=8, iqr_multiplier=1.5)
    options.update(overrides)
    return outlier_sql(**options)


def compare(**overrides):
    options = dict(
        schema="raw", table="delivery",
        segments=["Способ доставки", "Перевозчик"],
        metric="Стоимость доставки, руб.", date_column="Дата отправки",
        first_start=date(2025, 2, 1), first_end=date(2025, 3, 1),
        second_start=date(2025, 3, 1), second_end=date(2025, 4, 1),
    )
    options.update(overrides)
    return segment_comparison_sql(**options)


class SegmentQualityTests(unittest.TestCase):
    def test_quartiles_and_outlier_fences(self):
        stmt, params = outlier()
        expression = stmt.as_string()
        self.assertIn("PERCENTILE_CONT(0.25)", expression)
        self.assertIn("PERCENTILE_CONT(0.75)", expression)
        self.assertIn("q.q1 - (q.q3 - q.q1) * cfg.multiplier", expression)
        self.assertIn("q.q3 + (q.q3 - q.q1) * cfg.multiplier", expression)
        self.assertEqual(params, (8, 1.5, 100))
        self.assertIn("insufficient_sample", expression)

    def test_one_and_three_segment_dimensions(self):
        for fields in (["Канал"], ["Канал", "Город", "Тип"]):
            with self.subTest(fields=fields):
                statement, _ = outlier(segments=fields)
                sql_text = statement.as_string()
                for field in fields:
                    self.assertIn('"' + field + '"', sql_text)

    def test_invalid_number_not_counted_as_zero(self):
        expression = outlier()[0].as_string()
        self.assertIn("pg_input_is_valid(", expression)
        self.assertIn("COUNT(measure) AS valid_count", expression)
        self.assertIn("MAX(f.row_count) - MAX(f.valid_count)", expression)
        self.assertIn("b.measure IS NOT NULL", expression)

    def test_period_filter_parameterized(self):
        statement, params = outlier(
            date_column="Отправлено",
            date_kind="timestamp",
            start=date(2025, 2, 1), end=date(2025, 3, 1),
            limit=None,
        )
        self.assertIn("timestamp without time zone", statement.as_string())
        self.assertEqual(params, (8, 1.5, date(2025, 2, 1), date(2025, 3, 1)))
        self.assertNotIn("LIMIT %s", statement.as_string())

    def test_malformed_dates_rejected(self):
        for options in (
            dict(date_column="Отправлено", start=date(2025, 2, 1)),
            dict(start=date(2025, 2, 1), end=date(2025, 3, 1)),
            dict(date_column="Отправлено", start=date(2025, 3, 1),
                 end=date(2025, 2, 1)),
            dict(date_column="Отправлено", date_kind="numeric",
                 start=date(2025, 2, 1), end=date(2025, 3, 1)),
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                outlier(**options)

    def test_invalid_dimensions_and_limits(self):
        for segments in ([], ["A", "B", "C", "D"], ["A", "A"]):
            with self.subTest(segments=segments), self.assertRaises(ValueError):
                outlier(segments=segments)
        for limit in (0, 201, True, "20"):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                outlier(limit=limit)

    def test_multiplier_and_sample_safety(self):
        for multiplier in (0, -1, float("inf"), float("nan"), True, "1.5"):
            with self.subTest(multiplier=multiplier), self.assertRaises(ValueError):
                outlier(iqr_multiplier=multiplier)
        for size in (0, 3, True, 2.5):
            with self.subTest(size=size), self.assertRaises(ValueError):
                outlier(min_sample=size)

    def test_identifier_quote_and_values_parameterized(self):
        statement, params = outlier(
            schema="raw",
            table='orders"; DROP TABLE secret; --',
            segments=['A" weird'],
            metric='x" DROP TABLE',
        )
        expression = statement.as_string()
        self.assertIn('"raw"."orders""; DROP TABLE secret; --"', expression)
        self.assertIn('"A"" weird"', expression)
        self.assertIn('"x"" DROP TABLE"', expression)
        self.assertEqual(params, (8, 1.5, 100))

    def test_compare_groupings_and_null_safe_growth(self):
        stmt, params = compare()
        expression = stmt.as_string()
        self.assertIn("COUNT(measure) FILTER", expression)
        self.assertIn("AVG(measure) FILTER", expression)
        self.assertIn("SUM(measure) FILTER", expression)
        self.assertIn("NULLIF(g.avg_1, 0)", expression)
        self.assertIn("g.valid_1 >= cfg.min_sample", expression)
        self.assertIn("g.valid_2 >= cfg.min_sample", expression)
        self.assertIn('"Способ доставки"', expression)
        self.assertIn('"Перевозчик"', expression)
        self.assertEqual(params[1:5], (
            date(2025, 2, 1), date(2025, 3, 1),
            date(2025, 3, 1), date(2025, 4, 1),
        ))

    def test_compare_rejects_overlap(self):
        with self.assertRaises(ValueError):
            compare(second_start=date(2025, 2, 20))

    def test_compare_uses_correct_units(self):
        expression = compare()[0].as_string()
        self.assertIn('"Среднее 1"', expression)
        self.assertIn('"Среднее 2"', expression)
        self.assertIn('"Изменение, %"', expression)
        self.assertIn('"Пропусков или ошибок 1"', expression)
        self.assertIn('"Пропусков или ошибок 2"', expression)

    def test_compare_zero_baseline_is_undefined(self):
        statement = compare()[0].as_string()
        self.assertIn("NULLIF(g.avg_1, 0)", statement)

    def test_non_system_schema(self):
        for schema in ("pg_catalog", "information_schema"):
            with self.subTest(schema=schema), self.assertRaises(ValueError):
                outlier(schema=schema)

    def test_reference_iqr_on_small_in_memory_sample(self):
        # Values with Q1=2, Q3=4 (linear percentile), fences -1 and 7.
        values = [1, 2, 2, 3, 4, 4, 5, 100]
        def percentile(xs, p):
            k = (len(xs) - 1) * p
            lo = int(k)
            hi = min(lo + 1, len(xs) - 1)
            return xs[lo] + (k - lo) * (xs[hi] - xs[lo])
        q1, q3 = percentile(values, .25), percentile(values, .75)
        flagged = [v for v in values if v < q1 - 1.5 * (q3-q1)
                   or v > q3 + 1.5 * (q3-q1)]
        self.assertEqual(flagged, [100])


if __name__ == "__main__":
    unittest.main()
