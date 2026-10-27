"""Datasource management service for external PostgreSQL databases (BI-09 / Issue #37).

Provides connection testing with strict read-only guarantees, user table discovery,
persistent configuration storage in data/datasources.json, SSRF host restrictions,
and robust encryption of stored credentials.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import logging
import os
import re
import socket
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
import psycopg

from ..api.schemas import (
    DataSourceCreateRequest,
    DataSourceResponse,
    DataSourceTestRequest,
    DataSourceTestResponse,
)
from ..config import get_settings

logger = logging.getLogger(__name__)

DEFAULT_CONNECT_TIMEOUT = 3
READ_ONLY_OPTIONS = "-c default_transaction_read_only=on -c statement_timeout=5000"
MASKED_PASSWORD = "******"

FORBIDDEN_HOST_PATTERNS = {
    "169.254.169.254",
    "metadata.google.internal",
    "instance-data",
    "metadata.local",
}

CYRILLIC_TO_LATIN = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def slugify_datasource_name(name: str) -> str:
    """Convert display name into a clean, safe ASCII identifier using transliteration."""
    if not name:
        return ""
    res: list[str] = []
    for char in name.strip().lower():
        if char in CYRILLIC_TO_LATIN:
            res.append(CYRILLIC_TO_LATIN[char])
        elif char.isalnum() or char in ("_", "-"):
            res.append(char)
        else:
            res.append("_")
    slug = re.sub(r"_+", "_", "".join(res)).strip("_")
    return slug


def get_datasources_path() -> Path:
    """Resolve the path to data/datasources.json, ensuring parent directory exists."""
    settings = get_settings()
    configured = getattr(settings, "datasources_path", "data/datasources.json")
    p = Path(configured)
    if p.is_file() or p.parent.is_dir():
        return p
    p_up = Path("..") / configured
    if p_up.is_file() or p_up.parent.is_dir():
        return p_up
    root = Path(__file__).resolve().parents[3] / configured
    root.parent.mkdir(parents=True, exist_ok=True)
    return root


def _get_encryption_key() -> bytes:
    """Derive or load a 32-byte urlsafe base64 key for symmetric Fernet encryption."""
    env_key = os.getenv("DATASOURCES_SECRET_KEY") or os.getenv("APP_SECRET_KEY")
    if env_key:
        digest = hashlib.sha256(env_key.encode("utf-8")).digest()
        return base64.urlsafe_b64encode(digest)

    data_dir = get_datasources_path().parent
    key_file = data_dir / ".datasources_secret.key"
    if key_file.is_file():
        try:
            content = key_file.read_bytes().strip()
            if len(content) == 44:
                return content
        except Exception:
            pass

    new_key = Fernet.generate_key()
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        key_file.write_bytes(new_key)
    except Exception as exc:
        logger.warning("Could not persist secret key to %s: %s", key_file, exc)
    return new_key


def encrypt_secret(secret: str | None) -> str:
    """Encrypt secret into Fernet token. Empty or None strings return empty string."""
    if not secret:
        return ""
    try:
        fernet = Fernet(_get_encryption_key())
        return fernet.encrypt(secret.encode("utf-8")).decode("utf-8")
    except Exception as exc:
        logger.error("Failed to encrypt secret: %s", exc)
        return str(secret)


def decrypt_secret(token: str | None) -> str:
    """Decrypt Fernet token into plaintext secret. Fallback to raw token if unencrypted."""
    if not token:
        return ""
    if token.startswith("gAAAAA"):
        try:
            fernet = Fernet(_get_encryption_key())
            return fernet.decrypt(token.encode("utf-8")).decode("utf-8")
        except InvalidToken:
            logger.warning("Invalid token when decrypting datasource secret; returning raw value")
            return token
        except Exception as exc:
            logger.error("Error decrypting secret: %s", exc)
            return token
    return token


def validate_host_safety(host: str) -> None:
    """Validate target host against SSRF attacks (link-local, cloud metadata, multicast, etc.)."""
    if not host or not isinstance(host, str):
        raise ValueError("Не указан адрес хоста")
    host_clean = host.strip().lower()

    if host_clean in FORBIDDEN_HOST_PATTERNS or host_clean.startswith("169.254."):
        raise ValueError(
            f"Подключение к запрещённому адресу ({host}) заблокировано политикой безопасности SSRF."
        )

    # Check if host is direct IP
    try:
        ip = ipaddress.ip_address(host_clean)
        if ip.is_loopback:
            return
        if ip.is_link_local:
            raise ValueError(
                f"Подключение к link-local адресам ({host}) заблокировано политикой безопасности."
            )
        if ip.is_multicast:
            raise ValueError(
                f"Подключение к multicast адресам ({host}) заблокировано."
            )
        if ip.is_unspecified:
            raise ValueError(f"Недопустимый адрес хоста: {host}")
        if ip.is_reserved:
            raise ValueError(
                f"Подключение к зарезервированным адресам ({host}) заблокировано."
            )
        return
    except ValueError as exc:
        if "заблокировано" in str(exc) or "Недопустимый" in str(exc):
            raise

    # Resolve hostname if possible to verify resolved IPs against link-local/multicast
    try:
        addr_info = socket.getaddrinfo(host_clean, None)
        for _, _, _, _, sockaddr in addr_info:
            ip_str = sockaddr[0]
            ip = ipaddress.ip_address(ip_str)
            if ip.is_loopback:
                continue
            if ip.is_link_local:
                raise ValueError(
                    f"Разрешённый адрес ({ip_str}) для хоста {host} является link-local и заблокирован."
                )
            if ip.is_multicast:
                raise ValueError(
                    f"Разрешённый адрес ({ip_str}) для хоста {host} является multicast и заблокирован."
                )
            if ip.is_unspecified:
                raise ValueError(f"Недопустимый разрешённый адрес ({ip_str}) для хоста {host}.")
            if ip.is_reserved:
                raise ValueError(
                    f"Разрешённый адрес ({ip_str}) для хоста {host} является зарезервированным."
                )
    except (socket.gaierror, socket.herror, OSError):
        # Host could not be resolved right now (e.g. test mock host or offline);
        # actual psycopg connection will raise connection error cleanly.
        pass


def load_datasources_file() -> list[dict[str, Any]]:
    """Load stored data sources from JSON file. Return empty list if missing or invalid."""
    path = get_datasources_path()
    if not path.is_file():
        return []
    try:
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            return []
        data = json.loads(content)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "datasources" in data and isinstance(data["datasources"], list):
            return data["datasources"]
        return []
    except Exception as exc:
        logger.warning("Failed to read datasources file %s: %s", path, exc)
        return []


def save_datasources_file(datasources: list[dict[str, Any]]) -> None:
    """Save data source list persistently to JSON file with atomic write and encrypted passwords."""
    path = get_datasources_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".json.tmp")

    # Ensure all sensitive passwords are encrypted on disk
    secure_entries = []
    for item in datasources:
        if not isinstance(item, dict):
            continue
        entry = dict(item)
        raw_pwd = entry.get("password")
        if raw_pwd and not str(raw_pwd).startswith("gAAAAA"):
            entry["password"] = encrypt_secret(str(raw_pwd))
        secure_entries.append(entry)

    data_str = json.dumps(secure_entries, ensure_ascii=False, indent=2)
    tmp_path.write_text(data_str, encoding="utf-8")
    os.replace(tmp_path, path)


def sanitize_message(msg: str, password: str | None) -> str:
    """Strip raw password and connection credentials from error or status message."""
    if not msg:
        return ""
    clean = msg
    if password:
        if len(password) >= 3:
            clean = clean.replace(password, MASKED_PASSWORD)
        else:
            clean = re.sub(r"\b" + re.escape(password) + r"\b", MASKED_PASSWORD, clean)
    clean = re.sub(r"://([^:@\s]+):([^@\s]+)@", r"://\1:******@", clean)
    clean = re.sub(
        r"(?:password|pwd)\s*=\s*['\"]?[^\s'\"]+['\"]?",
        "password=******",
        clean,
        flags=re.IGNORECASE,
    )
    return clean


def test_postgres_connection(
    host: str,
    port: int,
    dbname: str,
    user: str,
    password: str = "",
    schema_name: str = "public",
    sslmode: str | None = None,
    connect_timeout: int = DEFAULT_CONNECT_TIMEOUT,
) -> tuple[bool, float, list[str], str]:
    """Connect to target PostgreSQL using psycopg with strict read-only options and timeout.

    Conforms to Technical Criterion 1:
    - connect_timeout=3
    - options includes -c default_transaction_read_only=on
    - Discovers accessible user tables and views from information_schema.tables (case-insensitive)
    - Validates host safety against SSRF
    - Checks whether role has superuser privileges (warning)
    - Never leaks plain password in error messages

    Returns:
        tuple[bool, float, list[str], str]: (success, latency_ms, table_names, status_message)
    """
    start_time = time.perf_counter()

    try:
        validate_host_safety(host)
    except ValueError as exc:
        latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
        return False, latency_ms, [], f"Ошибка валидации адреса хоста: {exc}"

    conn_kwargs: dict[str, Any] = {
        "host": host,
        "port": port,
        "dbname": dbname,
        "user": user,
        "password": password,
        "connect_timeout": connect_timeout,
        "autocommit": True,
        "options": READ_ONLY_OPTIONS,
    }
    if sslmode:
        conn_kwargs["sslmode"] = sslmode

    try:
        is_superuser = False
        with psycopg.connect(**conn_kwargs) as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        "SELECT rolsuper FROM pg_roles WHERE rolname = current_user;"
                    )
                    role_row = cur.fetchone()
                    if role_row and role_row[0]:
                        is_superuser = True
                except Exception:
                    pass

                target_schema = (schema_name or "").strip()
                if target_schema:
                    cur.execute(
                        """
                        SELECT table_name
                        FROM information_schema.tables
                        WHERE lower(table_schema) = lower(%s)
                          AND table_type IN ('BASE TABLE', 'VIEW')
                        ORDER BY table_name;
                        """,
                        (target_schema,),
                    )
                else:
                    cur.execute(
                        """
                        SELECT table_name
                        FROM information_schema.tables
                        WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
                          AND table_type IN ('BASE TABLE', 'VIEW')
                        ORDER BY table_name;
                        """
                    )
                rows = cur.fetchall()
                tables = [str(r[0]) for r in rows]

        latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
        msg = f"Успешное подключение. Доступно таблиц: {len(tables)}"
        if is_superuser:
            msg += " (Внимание: роль обладает правами superuser; рекомендуется выделить отдельную read-only роль)"
        return True, latency_ms, tables, msg

    except Exception as exc:
        latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
        safe_err = sanitize_message(str(exc), password)
        logger.warning("PostgreSQL connection test failed (%s ms): %s", latency_ms, safe_err)
        return False, latency_ms, [], f"Ошибка подключения к PostgreSQL: {safe_err}"


def test_datasource(req: DataSourceTestRequest) -> DataSourceTestResponse:
    """Execute connection test probe for requested PostgreSQL database."""
    success, latency, tables, message = test_postgres_connection(
        host=req.host,
        port=req.port,
        dbname=req.dbname,
        user=req.user,
        password=req.password or "",
        schema_name=req.schema_name,
        sslmode=req.sslmode,
    )
    return DataSourceTestResponse(
        status="ok" if success else "error",
        success=success,
        latency_ms=latency,
        tables_count=len(tables),
        tables=tables,
        message=message,
    )


def get_raw_datasource() -> DataSourceResponse:
    """Return default 'raw' local dataset metadata with masked password."""
    settings = get_settings()
    tables: list[str] = []
    try:
        from .agent import load_catalogs

        cat, _ = load_catalogs()
        if cat and isinstance(cat.get("tables"), list):
            tables = [t["name"] for t in cat["tables"] if isinstance(t, dict) and t.get("name")]
    except Exception:
        pass

    return DataSourceResponse(
        id="raw",
        name="Основная база данных (raw)",
        type="postgresql",
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=settings.postgres_db,
        user=settings.postgres_reader_user,
        password=MASKED_PASSWORD,
        schema_name=settings.postgres_schema,
        tables_count=len(tables),
        tables=tables,
        is_default=True,
        created_at="2026-10-25T00:00:00Z",
        description="Встроенный локальный датасет хакатона (PostgreSQL, схема raw)",
    )


def get_all_datasources() -> list[DataSourceResponse]:
    """List all available data sources (including default 'raw') with masked passwords."""
    raw_ds = get_raw_datasource()
    stored = load_datasources_file()
    result: list[DataSourceResponse] = [raw_ds]

    for item in stored:
        if not isinstance(item, dict):
            continue
        ds_id = item.get("id")
        if not ds_id or ds_id == "raw":
            continue

        raw_tables = item.get("tables")
        tables = [str(t) for t in raw_tables] if isinstance(raw_tables, list) else []
        tables_count = len(tables) if tables else int(item.get("tables_count", 0))

        result.append(
            DataSourceResponse(
                id=str(ds_id),
                name=str(item.get("name") or ds_id),
                type=str(item.get("type") or "postgresql"),
                host=str(item.get("host") or "localhost"),
                port=int(item.get("port") or 5432),
                dbname=str(item.get("dbname") or ""),
                user=str(item.get("user") or ""),
                password=MASKED_PASSWORD,
                schema_name=str(item.get("schema_name") or "public"),
                tables_count=tables_count,
                tables=tables,
                is_default=False,
                created_at=item.get("created_at"),
                description=item.get("description"),
            )
        )

    return result


def get_datasource_credentials(ds_id: str) -> dict[str, Any] | None:
    """Retrieve decrypted connection parameters for a registered external data source."""
    if not ds_id or ds_id.lower() == "raw":
        return None
    stored = load_datasources_file()
    for item in stored:
        if isinstance(item, dict) and str(item.get("id")).lower() == ds_id.lower():
            res = dict(item)
            res["password"] = decrypt_secret(res.get("password"))
            return res
    return None


def get_datasource_connection(ds_id: str) -> tuple[psycopg.Connection, str, dict[str, Any]]:
    """Create a validated read-only connection to an external data source."""
    creds = get_datasource_credentials(ds_id)
    if not creds:
        raise ValueError(f"Источник данных «{ds_id}» не найден в зарегистрированных источниках.")

    host = str(creds.get("host") or "localhost")
    port = int(creds.get("port") or 5432)
    dbname = str(creds.get("dbname") or "")
    user = str(creds.get("user") or "")
    password = str(creds.get("password") or "")
    schema_name = str(creds.get("schema_name") or "public")
    sslmode = creds.get("sslmode")

    validate_host_safety(host)

    conn_kwargs: dict[str, Any] = {
        "host": host,
        "port": port,
        "dbname": dbname,
        "user": user,
        "password": password,
        "connect_timeout": DEFAULT_CONNECT_TIMEOUT,
        "autocommit": True,
        "options": READ_ONLY_OPTIONS,
    }
    if sslmode:
        conn_kwargs["sslmode"] = sslmode

    conn = psycopg.connect(**conn_kwargs)
    return conn, schema_name, creds


def create_datasource(req: DataSourceCreateRequest) -> DataSourceResponse:
    """Validate connection and register an external PostgreSQL source with encrypted password."""
    # 1. Validate connection (which internally validates host safety)
    success, latency, tables, message = test_postgres_connection(
        host=req.host,
        port=req.port,
        dbname=req.dbname,
        user=req.user,
        password=req.password or "",
        schema_name=req.schema_name,
        sslmode=req.sslmode,
    )
    if not success:
        raise ValueError(message)

    # 2. Determine unique identifier
    stored = load_datasources_file()
    if req.id:
        ds_id = req.id.strip()
        if ds_id.lower() == "raw":
            raise ValueError("Идентификатор 'raw' зарезервирован для встроенного датасета")
    else:
        slug = slugify_datasource_name(req.name)
        if not slug or slug.lower() == "raw":
            slug = f"ds_{uuid.uuid4().hex[:8]}"
        stored_ids = {str(item.get("id")) for item in stored if isinstance(item, dict)}
        base_slug = slug
        counter = 2
        while slug in stored_ids:
            slug = f"{base_slug}_{counter}"
            counter += 1
        ds_id = slug

    # 3. Store with securely encrypted password
    now_iso = datetime.now(timezone.utc).isoformat()
    encrypted_pwd = encrypt_secret(req.password or "")

    new_entry: dict[str, Any] = {
        "id": ds_id,
        "name": req.name,
        "type": "postgresql",
        "host": req.host,
        "port": req.port,
        "dbname": req.dbname,
        "user": req.user,
        "password": encrypted_pwd,
        "schema_name": req.schema_name,
        "sslmode": req.sslmode,
        "tables": tables,
        "tables_count": len(tables),
        "is_default": False,
        "created_at": now_iso,
        "description": req.description,
    }

    updated = False
    for i, item in enumerate(stored):
        if isinstance(item, dict) and item.get("id") == ds_id:
            stored[i] = new_entry
            updated = True
            break
    if not updated:
        stored.append(new_entry)

    save_datasources_file(stored)

    # BI-09/10: Auto-catalog refresh
    try:
        import sys
        repo_root = Path(__file__).resolve().parents[3]
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        from db.refresh_catalog import refresh_catalog
        refresh_catalog(Path('data'))
        logger.info('Catalog auto-refreshed for datasource: %s', ds_id)
    except Exception as exc:
        logger.warning('Catalog auto-refresh failed for %s: %s', ds_id, exc)


    # 4. Return public representation with masked credentials
    return DataSourceResponse(
        id=ds_id,
        name=req.name,
        type="postgresql",
        host=req.host,
        port=req.port,
        dbname=req.dbname,
        user=req.user,
        password=MASKED_PASSWORD,
        schema_name=req.schema_name,
        tables_count=len(tables),
        tables=tables,
        is_default=False,
        created_at=now_iso,
        description=req.description,
    )