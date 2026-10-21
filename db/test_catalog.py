"""Проверки контракта каталога (без подключения к БД)."""
import unittest

from catalog_db import build_catalog


def data():
    profile = {"schema": "raw", "generated_at_utc": "test-1",
               "tables": [{"name": "sales", "sampled_rows": 3, "columns": [
                   {"name": "Цена", "inferred_type": "decimal",
                    "confidence": 1, "examples": ["12,2"]},
                   {"name": "Дата", "inferred_type": "timestamp"},
                   {"name": "Код", "inferred_type": "identifier",
                    "examples": ["A-1"]},
               ]}]}
    audit = {"schema": "raw", "source_profile_generated_at_utc": "test-1",
             "statistics_are_full_column": True, "columns": [
                 {"table": "sales", "column": "Цена", "target_type": "decimal",
                  "status": "checked", "total_rows": 5,
                  "missing_rows": 1, "invalid_rows": 0, "convertible_rows": 4},
                 {"table": "sales", "column": "Дата", "target_type": "timestamp",
                  "status": "checked", "total_rows": 5,
                  "missing_rows": 0, "invalid_rows": 2, "convertible_rows": 3},
             ]}
    return profile, audit


class CatalogTests(unittest.TestCase):
    def test_policies(self):
        profile, audit = data()
        result = build_catalog(profile, audit)
        cols = result["tables"][0]["columns"]
        self.assertEqual([c["conversion"]["policy"] for c in cols],
                         ["safe_with_nulls", "blocked_invalid", "not_applicable"])
        self.assertEqual(result["summary"]["cast_candidates"], 2)
        self.assertEqual(result["summary"]["safe_with_nulls"], 1)
        self.assertEqual(result["summary"]["blocked_invalid"], 1)
        self.assertFalse(result["relationships_verified"])
        self.assertNotIn("examples", cols[0])

    def test_opt_in_examples(self):
        profile, audit = data()
        cols = build_catalog(profile, audit, include_examples=True)["tables"][0]["columns"]
        self.assertEqual(cols[0]["examples"], ["12,2"])

    def test_partial_audit(self):
        profile, audit = data()
        audit["columns"] = [audit["columns"][0]]
        cols = build_catalog(profile, audit)["tables"][0]["columns"]
        self.assertEqual(cols[1]["conversion"]["policy"], "unverified")

    def test_no_convertible_values(self):
        profile, audit = data()
        audit["columns"][0].update(missing_rows=5, convertible_rows=0)
        cols = build_catalog(profile, audit)["tables"][0]["columns"]
        self.assertEqual(cols[0]["conversion"]["policy"], "unverified")

    def test_stale_audit_rejected(self):
        profile, audit = data()
        audit["source_profile_generated_at_utc"] = "old"
        with self.assertRaises(ValueError):
            build_catalog(profile, audit)

    def test_invalid_counts_rejected(self):
        profile, audit = data()
        audit["columns"][0]["convertible_rows"] = 5
        with self.assertRaises(ValueError):
            build_catalog(profile, audit)

    def test_orphan_audit_rejected(self):
        profile, audit = data()
        audit["columns"][0]["column"] = "unknown_column"
        with self.assertRaises(ValueError):
            build_catalog(profile, audit)

    def test_duplicate_audit_rejected(self):
        profile, audit = data()
        audit["columns"].append(audit["columns"][0])
        with self.assertRaises(ValueError):
            build_catalog(profile, audit)


if __name__ == "__main__":
    unittest.main()
