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
from backend.app.services import dashboards, kpis, saved_queries


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


if __name__ == "__main__":
    unittest.main()
