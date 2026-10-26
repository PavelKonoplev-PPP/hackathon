# BI-09 — isolated metadata for external PostgreSQL

This module is the **data-layer portion**, not a complete external-datasource connection.

Use `build_external_catalog(conn, datasource_id="external1", schema="public", root=Path("data"))` from `db.external_catalog` with an **already authenticated read-only psycopg connection**. It profiles up to 30 base tables, performs type audits, then atomically writes `data/external-catalogs/external1/schema-catalog.json`. No samples, row values, or credentials are written. Existing `data/schema-catalog.json` for local `raw` remains unchanged. Use `read_external_catalog(Path("data"), "external1")` to retrieve the metadata.

Restrictions: ids are restricted to ASCII alphanumeric, underscore or dash (up to 64 chars); system schemas are blocked; no automatic JOIN verification; each type audit may scan complete columns, so execute as a background job. A source must be explicitly registered and authorized by backend before any call; **do not trust datasource_id or schema from arbitrary user input**. Connections should have database-level SELECT-only privileges and short timeouts. The module enforces read-only transaction mode but cannot turn a privileged login into a truly least-privileged role.

**Remaining backend work, owned jointly with Pluray:** select a registered source, securely retrieve (not persist plaintext) its credentials, enforce hostname/SSRF protections, use its real psycopg connection and schema in PostgresRunner; run this profile job and expose status to UI. Existing `data/datasources.json` stores credentials in plaintext and must be migrated before using real customer databases; do not expose BI-09 publicly in that state. Frontend must send `datasource_id` and display the selected source.

**Tests:** `python -m unittest discover -s db -p 'test_*.py'`. End-to-end verification requires a separate throwaway PostgreSQL with known records plus a chat request pointed to it and then to `raw`; neither the existing DB nor env values are changed by this PR.
