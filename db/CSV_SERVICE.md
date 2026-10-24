# CSV preview and import service (backend integration)

The existing CLI \`python db/load_csv.py\` stays backward compatible: UTF-8 BOM,
semicolon delimiter, TEXT columns, streaming COPY, and no replacement by default.

The reusable module \`db/csv_service.py\` adds two functions:

\`\`\`python
from pathlib import Path
from db.csv_service import preview_csv, import_uploaded_csv
from db.load_csv import connect

# FastAPI must first save UploadFile under a randomly named path in a private
# temporary folder, enforce file-size and request limits, and retain the
# original filename separately. Never let the user supply a filesystem path.
path = Path("/private/temporary/uploaded.csv")
preview = preview_csv(path, original_filename="orders.csv")

with connect() as owner_connection:
    result = import_uploaded_csv(
        owner_connection, path, original_filename="orders.csv",
        delimiter=preview["delimiter"], encoding=preview["encoding"],
    )
# Example result: {schema, table_name, row_count, columns, warnings}
\`\`\`

Supported input encodings: UTF-8, UTF-8 BOM, Windows-1251.
Supported delimiters: semicolon, comma, tab, pipe. Auto-detection uses
\`csv.Sniffer\`; specify delimiter explicitly for ambiguous files.

Security:
- User-supplied filename must be a basename ending in .csv; it is used only
  for table naming and audit metadata. A temporary path is server-generated.
- SQL table/column identifiers are quoted by psycopg; contents go through COPY.
- Uploads may only target the \`raw\` schema and require a separate write-capable
  connection; the analytical \`bi_reader\` credentials must NEVER be used for imports.
- Existing tables are protected unless \`replace=True\` is explicitly given;
  the UI should not expose replacement without further authorization.
- Preview reads a bounded sample, not the entire file; streaming import checks
  all rows and will fail atomically on malformed CSV rows.
- Backend still must enforce upload size, file-count, concurrency and resource
  limits, and safely manage temporary files.
- After the import, backend must refresh profiling/audits/catalogs before new
  data become available to the SQL agent. This service does not perform that
  refresh on its own.

Run unit tests (no database required):
\`\`\`bash
python -m unittest discover -s db -p 'test_*.py'
\`\`\`
