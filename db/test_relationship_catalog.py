"""Тесты финального каталога связей (без живой базы)."""
import unittest

from relationship_catalog import build_relationship_catalog, parse_group


def data():
    relations = {
        "schema": "raw", "generated_at_utc": "stamp-1",
        "identifiers": [
            {"table": "sales", "column": "Код позиции", "dominant_prefix": "A-"},
            {"table": "sales", "column": "Код исходной позиции", "dominant_prefix": "A-"},
            {"table": "sales", "column": "Код магазина", "dominant_prefix": None},
            {"table": "jobs", "column": "Код цепочки", "dominant_prefix": "B-"},
        ],
        "relationships": [
            {"source": {"table": "sales", "column": "Код исходной позиции"},
             "target": {"table": "sales", "column": "Код позиции"},
             "kind": "self_reference_candidate",
             "status": "technical_reference_verified",
             "referential_integrity_verified": True,
             "target_unique_nonnull": True,
             "matched_source_rows": 10, "unmatched_source_rows": 0},
        ],
    }
    audit = {
        "schema": "raw",
        "generated_at_utc": "stamp-2",
        "source_relationships_generated_at_utc": "stamp-1",
        "identifiers": [
            {"table": "sales", "column": "Код магазина", "status": "checked",
             "total_rows": 50, "missing_rows": 40, "distinct_keys": 2,
             "duplicate_rows": 8, "repeating_keys": 2,
             "rows_in_repeating_keys": 10, "max_rows_per_key": 7},
        ],
    }
    return relations, audit


class RelationshipCatalogTests(unittest.TestCase):
    def test_preserves_verified_but_disallows_auto_join(self):
        rel, audit = data()
        c = build_relationship_catalog(rel, audit)
        self.assertEqual(c["summary"]["technical_relationships"], 1)
        self.assertEqual(c["summary"]["approved_joins"], 0)
        edge = c["join_candidates"][0]
        self.assertEqual(edge["policy"], "manual_approval_required")
        self.assertFalse(edge["can_auto_join"])
        self.assertEqual(c["summary"]["grouping_fields_with_missing_values"], 1)
        self.assertEqual(c["grouping_fields"][0]["missing_fraction"], 0.8)

    def test_additional_grouping(self):
        rel, audit = data()
        extra = [{
            "table": "jobs", "column": "Код цепочки", "status": "checked",
            "total_rows": 500, "missing_rows": 0, "distinct_keys": 450,
            "duplicate_rows": 50, "repeating_keys": 40,
            "rows_in_repeating_keys": 90, "max_rows_per_key": 4,
        }]
        c = build_relationship_catalog(rel, audit, additional_groups=extra)
        self.assertEqual(c["summary"]["grouping_fields_checked"], 2)
        self.assertEqual(c["grouping_fields"][1]["column"], "Код цепочки")
        self.assertEqual(c["grouping_fields"][1]["evidence"]["max_rows_per_key"], 4)

    def test_reject_stale_audit(self):
        rel, audit = data()
        audit["source_relationships_generated_at_utc"] = "old"
        with self.assertRaises(ValueError):
            build_relationship_catalog(rel, audit)

    def test_reject_missing_audit(self):
        rel, audit = data()
        audit["identifiers"] = []
        with self.assertRaises(ValueError):
            build_relationship_catalog(rel, audit)

    def test_reject_broken_counts(self):
        rel, audit = data()
        audit["identifiers"][0]["duplicate_rows"] = 9
        with self.assertRaises(ValueError):
            build_relationship_catalog(rel, audit)

    def test_reject_duplicate_extra_group(self):
        rel, audit = data()
        with self.assertRaises(ValueError):
            build_relationship_catalog(rel, audit, additional_groups=[audit["identifiers"][0]])

    def test_malformed_group_specs(self):
        self.assertEqual(parse_group("table:column"), ("table", "column"))
        with self.assertRaises(ValueError):
            parse_group("table")
        with self.assertRaises(ValueError):
            parse_group("table:")


if __name__ == "__main__":
    unittest.main()
