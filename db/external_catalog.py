"""BI-09: isolated metadata catalogs for external PostgreSQL sources.

The caller supplies an existing read-only psycopg connection. Credentials are
NEVER accepted here, and external catalogs never overwrite data/schema-catalog.json.
"""
from __future__ import annotations
import json
import os
import re
import tempfile
from pathlib import Path

try:
    from .profile_db import profile_database
    from .audit_profile import audit_profile
    from .catalog_db import build_catalog
except ImportError:
    from profile_db import profile_database
    from audit_profile import audit_profile
    from catalog_db import build_catalog

SOURCE_ID = re.compile(r"\A[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}\Z")
FORBIDDEN = {"raw", "pg_catalog", "information_schema"}

def catalog_path(root: Path, datasource_id: str) -> Path:
    if not isinstance(datasource_id, str) or not SOURCE_ID.fullmatch(datasource_id):
        raise ValueError("Invalid datasource_id")
    if datasource_id.lower() in FORBIDDEN:
        raise ValueError("Reserved datasource_id")
    return Path(root) / "external-catalogs" / datasource_id / "schema-catalog.json"

def build_external_catalog(conn, *, datasource_id: str, schema: str,
                           root: Path = Path("data"), sample_size: int = 200,
                           max_tables: int = 30) -> dict:
    """Read-only profiling and full type audit. Does not persist passwords or rows."""
    destination = catalog_path(root, datasource_id)
    if not isinstance(schema, str) or not schema.strip() or len(schema) > 63:
        raise ValueError("Invalid schema")
    if schema.lower() in FORBIDDEN or schema.lower().startswith("pg_"):
        raise ValueError("System/internal schema is not supported")
    if not 1 <= max_tables <= 30:
        raise ValueError("max_tables must be in range 1..30")

    # Enforce read-only even when supplied a connection that isn't configured so.
    # Using autocommit=True is recommended for caller connections.
    with conn.transaction():
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute("SET LOCAL statement_timeout = '15000ms'")
        profile = profile_database(conn, schema=schema, sample_size=sample_size,
                                   max_tables=max_tables, include_examples=False)
        if not profile.get("tables"):
            raise ValueError("No accessible base tables in the selected schema")
        audit = audit_profile(conn, profile)
        catalog = build_catalog(profile, audit)

    catalog["datasource_id"] = datasource_id
    catalog["source_kind"] = "external_postgresql"
    catalog["relationships_verified"] = False
    catalog["audit_failed_columns"] = audit["summary"]["failed_columns"]

    # Publish only after validation; never write sensitive values or credentials.
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=destination.parent, prefix=".catalog-",
                                         suffix=".tmp", delete=False) as stream:
            tmp = Path(stream.name)
            json.dump(catalog, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, destination)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
    return {"datasource_id": datasource_id, "schema": schema,
            "tables": len(catalog["tables"]), "columns": catalog["summary"]["columns"],
            "failed_audits": catalog["audit_failed_columns"],
            "catalog_path": str(destination)}

def read_external_catalog(root: Path, datasource_id: str) -> dict | None:
    path = catalog_path(root, datasource_id)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("datasource_id") != datasource_id or data.get("source_kind") != "external_postgresql":
        raise ValueError("Mismatching external catalog")
    return data
