"""Business KPI service — wraps db/business_kpis.py for FastAPI (BI-32 / Issue #78).

Provides:
- Proposing conservative, verified business KPIs from schema catalog.
- Calculating live values via PostgresRunner in read-only mode.
- Formatting KPI values according to business conventions (RUB, %, hours, counts).
- Approving / signing off on proposed KPIs with persistent audit trail in data/kpi-definitions.json.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..config import get_settings

logger = logging.getLogger(__name__)


def _format_kpi_value(v: Any, unit: str | None = None) -> tuple[float | int | None, str | None]:
    """Convert raw SQL runner result into (numeric_val, human_formatted_string)."""
    if v is None:
        return None, None

    num_val: float | int | None = None
    try:
        if isinstance(v, Decimal):
            num_val = float(v)
            if num_val.is_integer() and unit in ("rows", "operations", "tickets", "clicks"):
                num_val = int(num_val)
        elif isinstance(v, (int, float)):
            num_val = v
        else:
            cleaned = str(v).replace(",", ".").replace(" ", "")
            f = float(cleaned)
            num_val = int(f) if f.is_integer() and unit in ("rows", "operations", "tickets", "clicks") else f
    except Exception:
        return None, str(v)

    if num_val is None:
        return None, str(v)

    # Format human-readable string based on unit
    unit_lower = (unit or "").lower()
    if unit_lower == "rub" or "руб" in unit_lower:
        formatted = f"{num_val:,.2f}".replace(",", " ").replace(".", ",") + " руб."
    elif unit_lower == "%":
        formatted = f"{num_val:.2f} %"
    elif unit_lower in ("hours", "h") or "час" in unit_lower:
        formatted = f"{num_val:.2f} ч"
    elif isinstance(num_val, int):
        formatted = f"{num_val:,}".replace(",", " ")
    else:
        formatted = f"{num_val:,.2f}".replace(",", " ").replace(".", ",")

    return num_val, formatted


def get_kpi_definitions_path() -> Path:
    """Resolve the path to data/kpi-definitions.json, ensuring parent directory exists."""
    settings = get_settings()
    configured = getattr(settings, "kpi_definitions_path", "data/kpi-definitions.json")
    p = Path(configured)
    if p.is_file() or p.parent.is_dir():
        return p
    p_up = Path("..") / configured
    if p_up.is_file() or p_up.parent.is_dir():
        return p_up
    root = Path(__file__).resolve().parents[3] / configured
    root.parent.mkdir(parents=True, exist_ok=True)
    return root


def load_saved_kpi_definitions() -> dict[str, Any] | None:
    """Load persisted KPI definitions from data/kpi-definitions.json if present."""
    p = get_kpi_definitions_path()
    if not p.is_file():
        return None
    try:
        content = p.read_text(encoding="utf-8").strip()
        if not content:
            return None
        data = json.loads(content)
        if isinstance(data, dict) and isinstance(data.get("kpis"), list):
            return data
    except Exception as exc:
        logger.warning("Failed to read KPI definitions from %s: %s", p, exc)
    return None


def save_kpi_definitions_file(data: dict[str, Any]) -> None:
    """Persist KPI definitions to data/kpi-definitions.json atomically."""
    path = get_kpi_definitions_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def get_kpis(datasource_id: str = "raw", calculate: bool = True) -> dict[str, Any]:
    """Load catalog, propose verified KPIs, optionally calculate live values via PostgresRunner.

    Returns dict matching BusinessKpisResponse schema.
    """
    from .saved_queries import _load_catalog_for_datasource
    from ..db.session import get_reader_connection
    try:
        from backend.llm.sql_runner import PostgresRunner
    except ImportError:
        from llm.sql_runner import PostgresRunner  # type: ignore

    try:
        from db.business_kpis import propose_kpis
    except ImportError:
        import sys
        root = Path(__file__).resolve().parents[3]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from db.business_kpis import propose_kpis

    cat = _load_catalog_for_datasource(datasource_id)
    if not cat or not isinstance(cat.get("tables"), list):
        return {
            "datasource_id": datasource_id,
            "schema_name": "raw",
            "kpis": [],
            "summary": {"proposed": 0, "approved": 0, "unavailable": 0, "total": 0},
        }

    # Generate baseline proposals from technical catalog
    kpi_payload = propose_kpis(cat)
    kpis: list[dict[str, Any]] = kpi_payload.get("kpis", [])
    schema_name = kpi_payload.get("schema", "raw")

    # Overlay any previously approved/persisted status from data/kpi-definitions.json
    persisted = load_saved_kpi_definitions()
    if persisted and isinstance(persisted.get("kpis"), list):
        persisted_by_id = {
            item["id"]: item for item in persisted["kpis"]
            if isinstance(item, dict) and "id" in item
        }
        for k in kpis:
            saved = persisted_by_id.get(k["id"])
            if saved:
                for attr in ("status", "requires_business_approval", "approved_by", "approved_at", "comment"):
                    if attr in saved:
                        k[attr] = saved[attr]

    tables_by_name = {t["name"]: t for t in cat["tables"] if isinstance(t, dict)}

    # Calculate live values if requested
    if calculate:
        conn = None
        target_schema = schema_name
        try:
            if datasource_id and datasource_id.lower() != "raw":
                from .datasources import get_datasource_connection
                conn, target_schema, _ = get_datasource_connection(datasource_id)
            else:
                conn = get_reader_connection()

            runner = PostgresRunner(conn, schema=target_schema, max_rows=50)

            for kpi in kpis:
                if kpi.get("status") in ("proposed", "approved") and kpi.get("expression"):
                    table = tables_by_name.get(kpi.get("table"))
                    if not table:
                        continue
                    try:
                        clean_expr = kpi["expression"].strip()
                        res = runner.run(table, f"SELECT {clean_expr} FROM t")
                        raw_v = res.rows[0][0] if res.rows and len(res.rows[0]) > 0 else None
                        num_v, fmt_v = _format_kpi_value(raw_v, unit=kpi.get("unit"))
                        kpi["value"] = num_v
                        kpi["formatted_value"] = fmt_v
                    except Exception as exc:
                        logger.warning("KPI calc failed for %s: %s", kpi.get("id"), exc)
                        kpi["warnings"] = list(kpi.get("warnings") or []) + [f"Ошибка вычисления: {exc}"]
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    summary = {
        "proposed": sum(1 for k in kpis if k.get("status") == "proposed"),
        "approved": sum(1 for k in kpis if k.get("status") == "approved"),
        "unavailable": sum(1 for k in kpis if k.get("status") == "unavailable"),
        "total": len(kpis),
    }

    return {
        "datasource_id": datasource_id,
        "schema_name": schema_name,
        "kpis": kpis,
        "summary": summary,
    }


def approve_kpi(
    kpi_id: str,
    approved_by: str,
    comment: str | None = None,
    datasource_id: str = "raw",
) -> dict[str, Any] | None:
    """Mark a proposed KPI definition as approved and save persistently."""
    current = get_kpis(datasource_id=datasource_id, calculate=False)
    kpis = current.get("kpis", [])
    target: dict[str, Any] | None = None
    for k in kpis:
        if k.get("id") == kpi_id:
            target = k
            break

    if target is None:
        return None

    now_iso = datetime.now(timezone.utc).isoformat()
    target["status"] = "approved"
    target["requires_business_approval"] = False
    target["approved_by"] = approved_by.strip()
    target["approved_at"] = now_iso
    if comment:
        target["comment"] = comment.strip()

    # Save to file
    save_data = {
        "version": 1,
        "schema": current.get("schema_name", "raw"),
        "updated_at_utc": now_iso,
        "kpis": kpis,
        "summary": {
            "proposed": sum(1 for k in kpis if k.get("status") == "proposed"),
            "approved": sum(1 for k in kpis if k.get("status") == "approved"),
            "unavailable": sum(1 for k in kpis if k.get("status") == "unavailable"),
            "total": len(kpis),
        },
    }
    save_kpi_definitions_file(save_data)
    return target
