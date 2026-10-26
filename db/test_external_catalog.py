"""BI-09 isolation tests, no database or secrets required."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from external_catalog import catalog_path, build_external_catalog, read_external_catalog


class ExternalCatalogTests(unittest.TestCase):
    def test_ids_reject_path_traversal_and_reserved(self):
        with tempfile.TemporaryDirectory() as root:
            for value in ("../../raw", "../x", "raw", "pg_catalog", "", "a/b", "a\\b"):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    catalog_path(Path(root), value)

    def test_different_sources_have_different_paths(self):
        root = Path("data")
        self.assertNotEqual(catalog_path(root, "sales"), catalog_path(root, "support"))

    def test_build_persists_metadata_without_touching_raw(self):
        with tempfile.TemporaryDirectory() as root:
            p = Path(root)
            raw = p / "schema-catalog.json"
            raw.write_text('{"local":true}')
            conn = Mock()
            conn.transaction.return_value.__enter__ = Mock()
            conn.transaction.return_value.__exit__ = Mock(return_value=False)
            profile = {"schema": "public", "tables": [{"name": "orders", "columns": []}]}
            audit = {"summary": {"failed_columns": 0}}
            result_catalog = {"schema": "public", "tables": [{"name": "orders"}],
                              "summary": {"columns": 0}}
            with patch("external_catalog.profile_database", return_value=profile) as pf, \
                 patch("external_catalog.audit_profile", return_value=audit) as af, \
                 patch("external_catalog.build_catalog", return_value=result_catalog):
                result = build_external_catalog(conn, datasource_id="external1", schema="public", root=p)
            self.assertEqual(result["tables"], 1)
            self.assertEqual(raw.read_text(), '{"local":true}')
            self.assertEqual(read_external_catalog(p, "external1")["datasource_id"], "external1")
            self.assertIsNone(read_external_catalog(p, "external2"))
            self.assertFalse("examples" in str(read_external_catalog(p, "external1")))
            conn.execute.assert_any_call("SET TRANSACTION READ ONLY")
            self.assertEqual(pf.call_args.kwargs["include_examples"], False)
            af.assert_called_once()

    def test_error_does_not_publish_a_catalog(self):
        with tempfile.TemporaryDirectory() as root:
            conn = Mock()
            conn.transaction.return_value.__enter__ = Mock()
            conn.transaction.return_value.__exit__ = Mock(return_value=False)
            with patch("external_catalog.profile_database", side_effect=RuntimeError("DB failure")):
                with self.assertRaises(RuntimeError):
                    build_external_catalog(conn, datasource_id="test", schema="public", root=Path(root))
            self.assertFalse(catalog_path(Path(root), "test").exists())

    def test_reject_system_schema(self):
        with self.assertRaises(ValueError):
            build_external_catalog(Mock(), datasource_id="test", schema="pg_catalog")


if __name__ == "__main__":
    unittest.main()
