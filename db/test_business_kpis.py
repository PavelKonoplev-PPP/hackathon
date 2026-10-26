"""Unit tests: BI-32 never fabricates missing KPI inputs."""
import unittest
from business_kpis import propose_kpis


def make_catalog(names=("Количество, шт.", "Цена до скидки за единицу, руб.", "Скидка, %"), policy="safe_complete"):
    return {"schema": "raw", "generated_at_utc": "t1", "tables": [{
        "name": "data_01_Продажи", "columns": [
            {"name": x, "postgres_type": "text", "inferred_type": "decimal",
             "conversion": {"policy": policy}} for x in names
        ],
    }]}


class BusinessKpiTests(unittest.TestCase):
    def test_available_sales_revenue_is_only_proposed(self):
        result = propose_kpis(make_catalog())
        revenue = next(x for x in result["kpis"] if x["id"] == "sales_revenue")
        self.assertEqual(revenue["status"], "proposed")
        self.assertTrue(revenue["requires_business_approval"])
        self.assertIn('SUM("Количество, шт."', revenue["expression"])
        self.assertIn("возврата", revenue["description"].lower())

    def test_missing_price_disables_formula(self):
        result = propose_kpis(make_catalog(("Количество, шт.", "Скидка, %")))
        revenue = next(x for x in result["kpis"] if x["id"] == "sales_revenue")
        self.assertEqual(revenue["status"], "unavailable")
        self.assertIsNone(revenue["expression"])

    def test_unverified_numeric_column_disables_formula(self):
        result = propose_kpis(make_catalog(policy="blocked_invalid"))
        revenue = next(x for x in result["kpis"] if x["id"] == "sales_revenue")
        self.assertEqual(revenue["status"], "unavailable")
        self.assertTrue(revenue["warnings"])

    def test_ctr_is_ratio_of_totals_and_never_divides_by_zero(self):
        result = propose_kpis({"schema": "raw", "tables": [{
            "name": "data_06_Рекламные_кампании", "columns": [
                {"name": "Клики", "postgres_type": "integer", "conversion": {}},
                {"name": "Показы", "postgres_type": "integer", "conversion": {}},
            ],
        }]})
        ctr = next(x for x in result["kpis"] if x["id"] == "ads_ctr")
        self.assertEqual(ctr["status"], "proposed")
        self.assertIn("NULLIF", ctr["expression"])
        self.assertIn("100.0 * SUM", ctr["expression"])

    def test_no_unknown_tables_get_fabricated_kpis(self):
        self.assertEqual(propose_kpis({"schema": "raw", "tables": []})["kpis"], [])

    def test_invalid_catalog(self):
        with self.assertRaises(ValueError):
            propose_kpis({"schema": "raw", "tables": "oops"})


if __name__ == "__main__":
    unittest.main()
