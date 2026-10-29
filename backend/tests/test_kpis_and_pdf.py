"""Unit and integration tests for Business KPIs (BI-32 #78), PDF Export (BI-40 #82),
and SQL security & CTE execution (BI-35 #80, #85).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.services import dashboards, datasources, kpis, saved_queries


class KpisAndPdfTests(unittest.TestCase):
    """Test suite for BI-32, BI-40, and saved query security/CTE fixes."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    # -----------------------------------------------------------------
    # BI-32 (Issue #78): Business KPI API & Approval
    # -----------------------------------------------------------------
    def test_business_kpis_list_and_approval(self) -> None:
        """Issue #78 (BI-32): GET /api/kpis returns proposed/approved KPIs and POST /api/kpis/{id}/approve works."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_kpi_file = Path(tmp_dir) / "kpi-definitions.json"
            with patch.object(kpis, "get_kpi_definitions_path", return_value=temp_kpi_file):
                # 1. GET /api/kpis without live calculation
                res_nocalc = self.client.get("/api/kpis?calculate=false")
                self.assertEqual(res_nocalc.status_code, 200)
                data_nocalc = res_nocalc.json()
                self.assertEqual(data_nocalc["schema_name"], "raw")
                self.assertEqual(len(data_nocalc["kpis"]), 8)
                self.assertEqual(data_nocalc["summary"]["proposed"], 8)
                self.assertEqual(data_nocalc["summary"]["approved"], 0)

                # Verify KPI item structure
                first_kpi = data_nocalc["kpis"][0]
                for field in ("id", "table", "title", "unit", "description", "status", "requires_business_approval"):
                    self.assertIn(field, first_kpi)

                # 2. Approve a KPI definition
                approve_payload = {
                    "approved_by": "Chief Financial Officer",
                    "comment": "Формула выручки согласована",
                }
                app_res = self.client.post("/api/kpis/sales_revenue/approve", json=approve_payload)
                self.assertEqual(app_res.status_code, 200)
                app_data = app_res.json()
                self.assertEqual(app_data["id"], "sales_revenue")
                self.assertEqual(app_data["status"], "approved")
                self.assertFalse(app_data["requires_business_approval"])

                # 3. GET /api/kpis reflects the approved status
                res_after = self.client.get("/api/kpis?calculate=false")
                self.assertEqual(res_after.status_code, 200)
                data_after = res_after.json()
                self.assertEqual(data_after["summary"]["approved"], 1)
                self.assertEqual(data_after["summary"]["proposed"], 7)

                # 4. Approving non-existent KPI returns 404
                bad_res = self.client.post("/api/kpis/nonexistent_kpi/approve", json=approve_payload)
                self.assertEqual(bad_res.status_code, 404)

    def test_business_kpis_live_calculation(self) -> None:
        """Issue #78 (BI-32): GET /api/kpis?calculate=true returns calculated values."""
        res = self.client.get("/api/kpis?calculate=true")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(len(data["kpis"]), 8)

        # Control values check on real database
        kpis_by_id = {k["id"]: k for k in data["kpis"]}
        self.assertEqual(kpis_by_id["sales_rows"]["value"], 500000)
        self.assertEqual(kpis_by_id["support_tickets"]["value"], 500000)
        self.assertAlmostEqual(float(kpis_by_id["sales_revenue"]["value"]), 2684326520.31, places=1)
        self.assertIsNotNone(kpis_by_id["sales_revenue"]["formatted_value"])
        self.assertIn("руб.", kpis_by_id["sales_revenue"]["formatted_value"])

    # -----------------------------------------------------------------
    # BI-40 (Issue #82): PDF Export Endpoints
    # -----------------------------------------------------------------
    def test_custom_report_pdf_export(self) -> None:
        """Issue #82 (BI-40): POST /api/export/pdf generates valid PDF with custom payload."""
        payload = {
            "title": "Отчёт по аналитическим данным",
            "subtitle": "Автоматическая выгрузка",
            "datasource_id": "raw",
            "kpis": [
                {
                    "title": "Выручка",
                    "formatted_value": "2 684 326 520,31 руб.",
                    "status": "approved",
                    "description": "Сумма продаж с учётом скидок",
                }
            ],
            "table": {
                "columns": ["Канал", "Выручка"],
                "rows": [["Online", 1500000], ["Retail", 1184326520]],
            },
            "chart": {
                "title": "Выручка по каналам",
                "type": "bar",
                "categories": ["Online", "Retail"],
                "series": [{"name": "Выручка", "data": [1500000, 1184326520]}],
            },
            "sql": "SELECT channel, SUM(amount) FROM raw.sales GROUP BY 1",
        }
        res = self.client.post("/api/export/pdf", json=payload)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers.get("content-type"), "application/pdf")
        self.assertTrue(res.content.startswith(b"%PDF"))
        self.assertGreater(len(res.content), 1000)

    def test_dashboard_pdf_export(self) -> None:
        """Issue #82 (BI-40): GET /api/dashboards/{id}/pdf exports saved dashboard as PDF."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_dash_file = Path(tmp_dir) / "dashboards.json"
            with patch.object(dashboards, "get_dashboards_path", return_value=temp_dash_file):
                # 404 for non-existent dashboard
                res_missing = self.client.get("/api/dashboards/nonexistent/pdf")
                self.assertEqual(res_missing.status_code, 404)

                # Create dashboard
                create_res = self.client.post("/api/dashboards", json={
                    "title": "Дашборд директора",
                    "description": "Ключевые метрики за текущий период",
                    "widgets": [
                        {"type": "metric", "title": "Выручка", "content": {"value": "2.68 млрд руб."}},
                        {"type": "text", "title": "Выводы", "content": {"text": "Целевые показатели достигнуты."}},
                    ],
                })
                self.assertEqual(create_res.status_code, 201)
                dash_id = create_res.json()["id"]

                # Export to PDF
                res_dash_pdf = self.client.get(f"/api/dashboards/{dash_id}/pdf")
                self.assertEqual(res_dash_pdf.status_code, 200)
                self.assertEqual(res_dash_pdf.headers.get("content-type"), "application/pdf")
                self.assertTrue(res_dash_pdf.content.startswith(b"%PDF"))
                self.assertGreater(len(res_dash_pdf.content), 1000)

    # -----------------------------------------------------------------
    # BI-35 (Issue #80 & Issue #85): SQL Security & CTE-t execution
    # -----------------------------------------------------------------
    def test_saved_query_security_rejects_ddl(self) -> None:
        """Issue #80 (BI-35): Validate_sql rejects dangerous DDL/DML when executing saved query."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_file = Path(tmp_dir) / "saved_queries.json"
            with patch.object(saved_queries, "get_saved_queries_path", return_value=temp_file):
                bad_create = self.client.post("/api/queries", json={
                    "title": "Опасный SQL",
                    "sql": "DROP TABLE raw.data_01_Продажи; SELECT 1",
                })
                self.assertEqual(bad_create.status_code, 201)
                bad_id = bad_create.json()["id"]

                bad_exec = self.client.post(f"/api/queries/{bad_id}/execute")
                self.assertEqual(bad_exec.status_code, 404)
                self.assertIn("отклонён", bad_exec.json().get("detail", ""))

    def test_saved_query_cte_t_execution_with_comment(self) -> None:
        """Issue #85 (BI-35): CTE alias 't' with header comment executes via PostgresRunner."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_file = Path(tmp_dir) / "saved_queries.json"
            with patch.object(saved_queries, "get_saved_queries_path", return_value=temp_file):
                cte_sql = "-- t = данные таблицы «data_01_Продажи»\nSELECT channel, COUNT(*) FROM t GROUP BY 1"
                create_res = self.client.post("/api/queries", json={
                    "title": "Запрос агента с комментарием t",
                    "sql": cte_sql,
                })
                self.assertEqual(create_res.status_code, 201)
                query_id = create_res.json()["id"]

                with patch("backend.app.services.saved_queries.PostgresRunner") as mock_runner_cls, \
                     patch("backend.app.db.session.get_reader_connection") as mock_conn, \
                     patch("backend.app.services.saved_queries._load_catalog_for_datasource") as mock_cat:
                    mock_conn.return_value = MagicMock()
                    mock_cat.return_value = {
                        "tables": [{
                            "name": "data_01_Продажи",
                            "display_name": "Продажи",
                            "columns": [{"name": "channel"}],
                        }]
                    }
                    mock_run_result = SimpleNamespace(
                        columns=["channel", "count"],
                        rows=[("Online", 400), ("Retail", 600)],
                    )
                    mock_runner_cls.return_value.run.return_value = mock_run_result

                    exec_res = self.client.post(f"/api/queries/{query_id}/execute")
                    self.assertEqual(exec_res.status_code, 200)
                    res_data = exec_res.json()
                    self.assertEqual(res_data["status"], "completed")
                    self.assertIn("выполнен", res_data["result"]["answer"])
                    self.assertEqual(len(res_data["result"]["table"]["rows"]), 2)


    # -----------------------------------------------------------------
    # BI-08/10 (Issue #91): Separate CSV Commit and Read-only Cataloging
    # -----------------------------------------------------------------
    def test_csv_upload_refresh_catalog_failure_preserves_committed_import(self) -> None:
        """Issue #91: If refresh_catalog fails after commit, endpoint returns 200 with catalog_status='failed'."""
        from backend.app.api import routes

        csv_content = b"col1;col2\nval1;val2\n"
        with patch.object(routes, "get_reader_connection") as mock_r_conn:
            mock_conn_r = MagicMock()
            mock_r_conn.return_value.__enter__.return_value = mock_conn_r

            with patch("db.csv_service.import_uploaded_csv") as mock_import, \
                 patch("db.refresh_catalog.refresh_catalog", side_effect=RuntimeError("Lock timeout in catalog")) as mock_refresh, \
                 patch.object(routes, "get_writer_connection") as mock_w_conn:
                mock_w = MagicMock()
                mock_w_conn.return_value.__enter__.return_value = mock_w
                mock_import.return_value = {
                    "schema": "raw",
                    "table_name": "data_survived_orders",
                    "row_count": 42,
                    "columns": ["col1", "col2"],
                    "warnings": [],
                }

                response = self.client.post(
                    "/api/csv/upload",
                    files={"file": ("survived_orders.csv", csv_content, "text/csv")},
                    data={"replace": "true"},
                )
                self.assertEqual(response.status_code, 200)
                data = response.json()
                self.assertEqual(data["status"], "ok")
                self.assertEqual(data["table_name"], "data_survived_orders")
                self.assertEqual(data["row_count"], 42)
                self.assertFalse(data["catalog_refreshed"])
                self.assertEqual(data["catalog_status"], "failed")
                self.assertTrue(any("Lock timeout" in w for w in data["warnings"]))
                # Verify writer committed BEFORE refresh was attempted
                mock_w.commit.assert_called_once()
                mock_refresh.assert_called_once_with(mock_conn_r, table="data_survived_orders", schema="raw")

    def test_csv_upload_partial_catalog_audit_reports_warnings(self) -> None:
        """Issue #91: Partial catalog verification (failed_audits > 0) sets catalog_status='partial' and warnings."""
        from backend.app.api import routes

        csv_content = b"col1;col2\nval1;val2\n"
        with patch.object(routes, "get_reader_connection") as mock_r_conn:
            mock_conn_r = MagicMock()
            mock_r_conn.return_value.__enter__.return_value = mock_conn_r

            with patch("db.csv_service.import_uploaded_csv") as mock_import, \
                 patch("db.refresh_catalog.refresh_catalog") as mock_refresh, \
                 patch.object(routes, "get_writer_connection") as mock_w_conn:
                mock_w = MagicMock()
                mock_w_conn.return_value.__enter__.return_value = mock_w
                mock_import.return_value = {
                    "schema": "raw",
                    "table_name": "data_partial_audit",
                    "row_count": 100,
                    "columns": ["col1", "col2"],
                    "warnings": [],
                }
                mock_refresh.return_value = {
                    "mode": "incremental",
                    "failed_audits": 2,
                    "unverified_columns": 3,
                    "tables": 10,
                }

                response = self.client.post(
                    "/api/csv/upload",
                    files={"file": ("partial_audit.csv", csv_content, "text/csv")},
                    data={"replace": "true"},
                )
                self.assertEqual(response.status_code, 200)
                data = response.json()
                self.assertEqual(data["status"], "ok")
                self.assertFalse(data["catalog_refreshed"])
                self.assertEqual(data["catalog_status"], "partial")
                self.assertTrue(any("ошибок аудита типов: 2" in w for w in data["warnings"]))
                self.assertIsNotNone(data["catalog_report"])
                self.assertEqual(data["catalog_report"]["failed_audits"], 2)

    def test_csv_upload_strictly_uses_readonly_connection_for_catalog(self) -> None:
        """Issue #91: Catalog profiling uses separate get_reader_connection, never the writer connection."""
        from backend.app.api import routes

        csv_content = b"col1;col2\nval1;val2\n"
        with patch.object(routes, "get_reader_connection") as mock_r_conn, \
             patch.object(routes, "get_writer_connection") as mock_w_conn, \
             patch("db.csv_service.import_uploaded_csv") as mock_import, \
             patch("db.refresh_catalog.refresh_catalog") as mock_refresh:
            mock_reader = MagicMock(name="reader_conn")
            mock_r_conn.return_value.__enter__.return_value = mock_reader

            mock_writer = MagicMock(name="writer_conn")
            mock_w_conn.return_value.__enter__.return_value = mock_writer

            mock_import.return_value = {
                "schema": "raw",
                "table_name": "data_orders",
                "row_count": 10,
                "columns": ["col1", "col2"],
                "warnings": [],
            }
            mock_refresh.return_value = {"failed_audits": 0, "unverified_columns": 0}

            response = self.client.post(
                "/api/csv/upload",
                files={"file": ("orders.csv", csv_content, "text/csv")},
                data={"replace": "true"},
            )
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertTrue(data["catalog_refreshed"])
            self.assertEqual(data["catalog_status"], "ready")

            # Check that writer was NEVER passed to refresh_catalog
            writer_passed = any(call.args and call.args[0] is mock_writer for call in mock_refresh.call_args_list)
            self.assertFalse(writer_passed, "Writer connection was erroneously passed to refresh_catalog!")

            # Check that reader was passed to refresh_catalog
            reader_passed = any(call.args and call.args[0] is mock_reader for call in mock_refresh.call_args_list)
            self.assertTrue(reader_passed, "Reader connection was NOT passed to refresh_catalog!")

    # -----------------------------------------------------------------
    # BI-09/10 (Issue #79): Auto-catalog refresh for external PostgreSQL
    # -----------------------------------------------------------------
    def test_external_datasource_create_auto_refreshes_catalog_with_connection(self) -> None:
        """Issue #79: create_datasource calls build_external_catalog with a DB connection, not a Path object."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_file = Path(tmp_dir) / "datasources.json"

            mock_conn = MagicMock(name="ext_db_conn")
            mock_report = {
                "datasource_id": "ext_dwh",
                "schema": "public",
                "tables": 2,
                "columns": 5,
                "failed_audits": 0,
                "catalog_path": str(Path(tmp_dir) / "external-catalogs" / "ext_dwh" / "schema-catalog.json"),
            }

            with patch.object(datasources, "get_datasources_path", return_value=temp_file), \
                 patch.object(datasources, "test_postgres_connection", return_value=(True, 5.0, ["orders", "users"], "OK")), \
                 patch.object(datasources, "get_datasource_connection", return_value=(mock_conn, "public", {})), \
                 patch("db.external_catalog.build_external_catalog", return_value=mock_report) as mock_build:

                response = self.client.post(
                    "/api/datasources",
                    json={
                        "id": "ext_dwh",
                        "name": "Enterprise DWH",
                        "host": "dwh.internal",
                        "port": 5432,
                        "dbname": "dwh_prod",
                        "user": "bi_reader",
                        "password": "secret_pwd",
                        "schema_name": "public",
                    },
                )
                self.assertEqual(response.status_code, 200)
                data = response.json()
                self.assertEqual(data["id"], "ext_dwh")
                self.assertEqual(data["password"], "******")

                # Verify build_external_catalog was called with mock_conn (NOT a Path!)
                mock_build.assert_called_once()
                args, kwargs = mock_build.call_args
                self.assertIs(args[0], mock_conn, "First arg to build_external_catalog must be the DB connection!")
                self.assertEqual(kwargs.get("datasource_id"), "ext_dwh")
                self.assertEqual(kwargs.get("schema"), "public")

                # Verify connection was closed
                mock_conn.close.assert_called_once()

    def test_external_datasource_create_catalog_failure_is_non_blocking(self) -> None:
        """Issue #79: If auto-catalog fails (e.g. network timeout), datasource creation still succeeds."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_file = Path(tmp_dir) / "datasources.json"

            with patch.object(datasources, "get_datasources_path", return_value=temp_file), \
                 patch.object(datasources, "test_postgres_connection", return_value=(True, 5.0, ["orders"], "OK")), \
                 patch.object(datasources, "get_datasource_connection", side_effect=RuntimeError("Connection refused by firewall")):

                response = self.client.post(
                    "/api/datasources",
                    json={
                        "id": "ext_unreachable",
                        "name": "Firewalled DB",
                        "host": "isolated.internal",
                        "port": 5432,
                        "dbname": "isolated_db",
                        "user": "bi_reader",
                        "password": "secret_pwd",
                        "schema_name": "public",
                    },
                )
                # Must return HTTP 200, logging warning rather than failing registration
                self.assertEqual(response.status_code, 200)
                data = response.json()
                self.assertEqual(data["id"], "ext_unreachable")
                self.assertTrue(temp_file.is_file())

    def test_external_datasource_refresh_endpoint(self) -> None:
        """Issue #79: POST /api/datasources/{id}/refresh builds catalog and returns report."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_file = Path(tmp_dir) / "datasources.json"

            mock_conn = MagicMock(name="ext_db_conn")
            mock_report = {
                "datasource_id": "ext_dwh",
                "schema": "public",
                "tables": 3,
                "columns": 10,
                "failed_audits": 0,
                "catalog_path": "data/external-catalogs/ext_dwh/schema-catalog.json",
            }

            with patch.object(datasources, "get_datasources_path", return_value=temp_file), \
                 patch.object(datasources, "get_datasource_connection", return_value=(mock_conn, "public", {})), \
                 patch("db.external_catalog.build_external_catalog", return_value=mock_report):

                # 1. Reject 'raw'
                raw_resp = self.client.post("/api/datasources/raw/refresh")
                self.assertEqual(raw_resp.status_code, 400)

                # 2. Refresh external datasource
                resp = self.client.post("/api/datasources/ext_dwh/refresh")
                self.assertEqual(resp.status_code, 200)
                data = resp.json()
                self.assertEqual(data["status"], "ok")
                self.assertEqual(data["datasource_id"], "ext_dwh")
                self.assertTrue(data["catalog_refreshed"])
                self.assertEqual(data["report"]["tables"], 3)
                mock_conn.close.assert_called_once()

    def test_external_datasource_e2e_real_catalog_file_and_schema_endpoint(self) -> None:
        """Issue #79: End-to-end registration writes catalog file and GET /api/schema serves it."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_file = Path(tmp_dir) / "datasources.json"

            mock_conn = MagicMock(name="ext_db_conn")
            mock_conn.transaction.return_value.__enter__ = MagicMock()
            mock_conn.transaction.return_value.__exit__ = MagicMock(return_value=False)

            fake_profile = {
                "schema": "public",
                "tables": [
                    {
                        "name": "crm_leads",
                        "row_count": 4200,
                        "columns": [
                            {"name": "lead_id", "inferred_type": "integer", "null_count": 0},
                            {"name": "status", "inferred_type": "text", "null_count": 5},
                        ],
                    }
                ],
            }
            fake_audit = {"summary": {"failed_columns": 0}, "columns": {}}
            fake_catalog = {
                "schema": "public",
                "summary": {"tables": 1, "columns": 2, "rows": 4200},
                "tables": [
                    {
                        "name": "crm_leads",
                        "row_count": 4200,
                        "columns": [
                            {"name": "lead_id", "data_type": "integer", "nullable": False},
                            {"name": "status", "data_type": "text", "nullable": True},
                        ],
                    }
                ],
            }

            with patch.object(datasources, "get_datasources_path", return_value=temp_file), \
                 patch.object(datasources, "test_postgres_connection", return_value=(True, 4.0, ["crm_leads"], "OK")), \
                 patch.object(datasources, "get_datasource_connection", return_value=(mock_conn, "public", {})), \
                 patch("db.external_catalog.profile_database", return_value=fake_profile), \
                 patch("db.external_catalog.audit_profile", return_value=fake_audit), \
                 patch("db.external_catalog.build_catalog", return_value=fake_catalog):

                # 1. Register datasource
                create_resp = self.client.post(
                    "/api/datasources",
                    json={
                        "id": "ext_crm",
                        "name": "CRM Leads System",
                        "host": "crm.internal",
                        "port": 5432,
                        "dbname": "crm_db",
                        "user": "bi_reader",
                        "password": "secret_pwd",
                        "schema_name": "public",
                    },
                )
                self.assertEqual(create_resp.status_code, 200)

                # 2. Verify catalog file was written on disk
                cat_file = Path(tmp_dir) / "external-catalogs" / "ext_crm" / "schema-catalog.json"
                self.assertTrue(cat_file.is_file(), f"Expected catalog file at {cat_file}")

                # 3. Verify GET /api/schema?datasource_id=ext_crm serves the generated catalog
                schema_resp = self.client.get("/api/schema?datasource_id=ext_crm")
                self.assertEqual(schema_resp.status_code, 200)
                schema_data = schema_resp.json()
                self.assertEqual(schema_data["schema_name"], "public")
                self.assertEqual(len(schema_data["tables"]), 1)
                self.assertEqual(schema_data["tables"][0]["name"], "crm_leads")
                self.assertEqual(schema_data["tables"][0]["row_count"], 4200)
                self.assertEqual(len(schema_data["tables"][0]["columns"]), 2)
                self.assertEqual(schema_data["tables"][0]["columns"][0]["name"], "lead_id")

                # 4. Verify saved_queries._load_catalog_for_datasource loads the catalog
                loaded_cat = saved_queries._load_catalog_for_datasource("ext_crm")
                self.assertIsNotNone(loaded_cat)
                self.assertEqual(loaded_cat["datasource_id"], "ext_crm")


if __name__ == "__main__":
    unittest.main()
