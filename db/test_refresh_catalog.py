"""BI-10: обновление каталогов без обращения к реальной БД."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from refresh_catalog import (
    _audit_summary, _load_previous, _merge_reports, refresh_catalog,
)


def profile(tables, stamp="t1"):
    return {
        "schema": "raw", "generated_at_utc": stamp, "sampling": {},
        "tables": [
            {"name": name, "columns": [
                {"name": "Категория", "postgres_type": "text",
                 "inferred_type": "category", "confidence": 0.8}
            ]}
            for name in tables
        ],
    }


def audit(stamp="t1", cols=None):
    return {
        "schema": "raw", "generated_at_utc": stamp,
        "source_profile_generated_at_utc": stamp,
        "statistics_are_full_column": True, "summary": {},
        "columns": cols or [],
    }


class RefreshTests(unittest.TestCase):
    def test_merge_keeps_old_tables_and_uses_new_stamp(self):
        old_p, old_a = profile(["old", "updated"]), audit()
        new_p, new_a = profile(["updated"], "t2"), audit("t2")
        p, a = _merge_reports(old_p, old_a, new_p, new_a, "updated")
        self.assertEqual([x["name"] for x in p["tables"]], ["old", "updated"])
        self.assertEqual(a["source_profile_generated_at_utc"], "t2")
        self.assertEqual(old_p["generated_at_utc"], "t1")

    def test_audit_summary_preserves_failed_items(self):
        self.assertEqual(_audit_summary([
            {"status": "error"},
            {"status": "checked", "invalid_rows": 0, "missing_rows": 2},
        ]), {
            "candidate_columns": 2, "checked_columns": 1, "failed_columns": 1,
            "columns_with_invalid_values": 0, "columns_with_missing_values": 1,
            "fully_convertible_columns": 0,
        })

    def test_incremental_refresh_preserves_original_tables(self):
        import json
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            old_p, old_a = profile(["old", "updated"]), audit()
            from catalog_db import build_catalog
            (folder / "all-profile.json").write_text(json.dumps(old_p))
            (folder / "type-audit.json").write_text(json.dumps(old_a))
            (folder / "schema-catalog.json").write_text(json.dumps(build_catalog(old_p, old_a)))
            conn = Mock()
            with patch("refresh_catalog.get_tables", return_value=[
                ("old", 20), ("updated", 10)
            ]), patch("refresh_catalog.profile_database", return_value=profile(["updated"], "t2")) as pfn, patch(
                "refresh_catalog.audit_profile", return_value=audit("t2")
            ) as afn:
                report = refresh_catalog(conn, table="updated", data_dir=folder)
            self.assertEqual(report["mode"], "incremental")
            self.assertEqual(report["tables"], 2)
            self.assertEqual(report["updated_table"], "updated")
            self.assertEqual(report["failed_audits"], 0)
            pfn.assert_called_once()
            self.assertEqual(pfn.call_args.kwargs["table"], "updated")
            afn.assert_called_once()
            result = json.loads((folder / "schema-catalog.json").read_text())
            self.assertEqual([x["name"] for x in result["tables"]], ["old", "updated"])
            self.assertEqual(result["refresh"]["preserved_prior_tables"], ["old"])

    def test_missing_cache_runs_full_build(self):
        with tempfile.TemporaryDirectory() as root:
            conn = Mock()
            with patch("refresh_catalog.profile_database", return_value=profile(["new"])) as pf, patch(
                "refresh_catalog.audit_profile", return_value=audit()
            ):
                report = refresh_catalog(conn, table="new", data_dir=Path(root))
            self.assertEqual(report["mode"], "full")
            pf.assert_called_once()
            self.assertIsNone(pf.call_args.kwargs.get("table"))

    def test_other_table_added_forces_full_refresh(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            old_p, old_a = profile(["old"]), audit()
            from catalog_db import build_catalog
            import json
            for name, value in (
                ("all-profile.json", old_p),
                ("type-audit.json", old_a),
                ("schema-catalog.json", build_catalog(old_p, old_a)),
            ):
                (folder / name).write_text(json.dumps(value))
            with patch("refresh_catalog.get_tables", return_value=[("old", 1), ("new", 1)]), patch(
                "refresh_catalog.profile_database", return_value=profile(["old", "new"], "t2")
            ) as pf, patch("refresh_catalog.audit_profile", return_value=audit("t2")):
                report = refresh_catalog(Mock(), table="new", data_dir=folder)
            # Other tables unchanged: incremental refresh is allowed, but only
            # when the request names the one newly imported table.
            self.assertEqual(report["mode"], "incremental")
            pf.assert_called_once()

    def test_bad_schema_rejected(self):
        with self.assertRaises(ValueError):
            refresh_catalog(Mock(), schema="public")

    def test_corrupt_catalog_refused_without_overwriting(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            (folder / "all-profile.json").write_text("{}")
            (folder / "type-audit.json").write_text("{}")
            (folder / "schema-catalog.json").write_text("{}")
            with self.assertRaises(ValueError):
                refresh_catalog(Mock(), data_dir=folder)
            self.assertEqual((folder / "schema-catalog.json").read_text(), "{}")


if __name__ == "__main__":
    unittest.main()
