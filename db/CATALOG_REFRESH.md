# BI-10: refresh metadata after upload

After the FastAPI upload endpoint has successfully committed the CSV import,
refresh the catalog using the *same schema*, but a **read-only** PostgreSQL
connection. This step is separate from the existing import transaction.

\`\`\`python
from pathlib import Path
from db.refresh_catalog import refresh_catalog
from db.profile_db import connect as connect_readonly

# Import path/filename are provided by the upload service.
# Import has already finished successfully, and new_table is the returned
# PostgreSQL table name (not user-controlled SQL).
with connect_readonly() as conn:
    result = refresh_catalog(
        conn, schema="raw", table=new_table,
        data_dir=Path("data"), sample_size=300,
    )
\`\`\`

Or manually, from the repository root:

\`\`\`bash
python db/refresh_catalog.py --table data_orders
# Full refresh, if the underlying tables have changed outside the importer:
python db/refresh_catalog.py
\`\`\`

The service preserves profile/audit results for unchanged tables when possible.
If it cannot trust an existing cache, it does not silently merge mismatched
reports. If a cache does not exist, it builds all tables.

**Important caveats:**
- Full column audits can take time. Backend should run catalog refresh via a
  background job/queue and expose its status, rather than block a web request.
- Avoid concurrently loading/editing other tables while profiling. Old-table
  metadata is reused in incremental mode and should be rebuilt if it changes.
- Return code 2 / \`failed_audits>0\` indicates partial verification. The
  resulting catalog marks unverified fields accordingly; do not treat the
  import as fully analytically verified.
- \`relationship-catalog.json\` is NOT rebuilt: backend must mark relationships
  stale and rerun identifier and relationship audits before permitting joins.
  Auto-join remains disabled in the current system.
- The catalog (plus profile and audit) is stored under \`data/\`, ignored by Git.
  Catalog JSON includes metadata only, not raw CSV data.
- Never call this with credentials embedded in SQL or expose the data directory
  to users. Only data already committed by the importer should be catalogued.
- The SQL-agent currently reads the catalog on each request, so an updated
  \`schema-catalog.json\` becomes available to subsequent questions.
- For external PostgreSQL sources, the catalog must later be keyed by
  \`datasource_id\`; the current service is for local schema \`raw\`.

Test without a running PostgreSQL:
\`\`\`bash
python -m unittest discover -s db -p 'test_*.py'
\`\`\`
