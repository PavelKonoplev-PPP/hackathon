"""Dashboard management service (BI-15 / Issue #43 / Scenario 5).

Provides persistent storage, retrieval, updating, and deletion of analytical dashboards
in data/dashboards.json with atomic writes and thread safety.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..api.schemas import (
    DashboardCreateRequest,
    DashboardDetail,
    DashboardSummary,
    DashboardUpdateRequest,
    DashboardWidget,
)
from ..config import get_settings

logger = logging.getLogger(__name__)

CYRILLIC_TO_LATIN = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def slugify_dashboard_title(title: str) -> str:
    """Convert dashboard title into a clean ASCII slug."""
    if not title:
        return ""
    res: list[str] = []
    for char in title.strip().lower():
        if char in CYRILLIC_TO_LATIN:
            res.append(CYRILLIC_TO_LATIN[char])
        elif char.isalnum() or char in ("_", "-"):
            res.append(char)
        else:
            res.append("_")
    slug = re.sub(r"_+", "_", "".join(res)).strip("_")
    return slug


def get_dashboards_path() -> Path:
    """Resolve the path to data/dashboards.json, ensuring parent directory exists."""
    settings = get_settings()
    configured = getattr(settings, "dashboards_path", "data/dashboards.json")
    p = Path(configured)
    if p.is_file() or p.parent.is_dir():
        return p
    p_up = Path("..") / configured
    if p_up.is_file() or p_up.parent.is_dir():
        return p_up
    root = Path(__file__).resolve().parents[3] / configured
    root.parent.mkdir(parents=True, exist_ok=True)
    return root


def load_dashboards_file() -> list[dict[str, Any]]:
    """Load stored dashboards from JSON file. Return empty list if missing or invalid."""
    path = get_dashboards_path()
    if not path.is_file():
        return []
    try:
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            return []
        data = json.loads(content)
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict) and "id" in d]
        return []
    except Exception as exc:
        logger.warning("Failed to parse dashboards file at %s: %s", path, exc)
        return []


def save_dashboards_file(records: list[dict[str, Any]]) -> None:
    """Atomically write dashboard records to data/dashboards.json."""
    path = get_dashboards_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    payload = json.dumps(records, ensure_ascii=False, indent=2)
    tmp_path.write_text(payload, encoding="utf-8")
    os.replace(tmp_path, path)


def list_dashboards() -> list[DashboardSummary]:
    """Return all saved dashboards as summaries."""
    records = load_dashboards_file()
    summaries: list[DashboardSummary] = []
    for rec in records:
        widgets = rec.get("widgets", [])
        w_count = len(widgets) if isinstance(widgets, list) else 0
        summaries.append(
            DashboardSummary(
                id=rec["id"],
                title=rec.get("title", "Untitled"),
                description=rec.get("description"),
                datasource_id=rec.get("datasource_id", "raw"),
                widget_count=w_count,
                created_at=rec.get("created_at") or datetime.now(timezone.utc).isoformat(),
                updated_at=rec.get("updated_at") or datetime.now(timezone.utc).isoformat(),
            )
        )
    return summaries


def get_dashboard(dashboard_id: str) -> DashboardDetail | None:
    """Get full details of a saved dashboard by its ID."""
    records = load_dashboards_file()
    for rec in records:
        if rec.get("id") == dashboard_id:
            raw_widgets = rec.get("widgets", [])
            widgets: list[DashboardWidget] = []
            if isinstance(raw_widgets, list):
                for w in raw_widgets:
                    if isinstance(w, dict):
                        widgets.append(DashboardWidget(**w))
            return DashboardDetail(
                id=rec["id"],
                title=rec.get("title", "Untitled"),
                description=rec.get("description"),
                datasource_id=rec.get("datasource_id", "raw"),
                widgets=widgets,
                created_at=rec.get("created_at") or datetime.now(timezone.utc).isoformat(),
                updated_at=rec.get("updated_at") or datetime.now(timezone.utc).isoformat(),
            )
    return None


def create_dashboard(req: DashboardCreateRequest) -> DashboardDetail:
    """Create a new dashboard and persist it."""
    records = load_dashboards_file()
    slug = slugify_dashboard_title(req.title)
    base_id = f"dash_{slug}" if slug else f"dash_{uuid.uuid4().hex[:8]}"

    existing_ids = {r.get("id") for r in records}
    final_id = base_id
    counter = 2
    while final_id in existing_ids:
        final_id = f"{base_id}_{counter}"
        counter += 1

    now = datetime.now(timezone.utc).isoformat()
    widgets_data = [w.model_dump() for w in req.widgets]

    record = {
        "id": final_id,
        "title": req.title,
        "description": req.description,
        "datasource_id": req.datasource_id,
        "widgets": widgets_data,
        "created_at": now,
        "updated_at": now,
    }
    records.append(record)
    save_dashboards_file(records)

    return DashboardDetail(
        id=final_id,
        title=req.title,
        description=req.description,
        datasource_id=req.datasource_id,
        widgets=req.widgets,
        created_at=now,
        updated_at=now,
    )


def update_dashboard(dashboard_id: str, req: DashboardUpdateRequest) -> DashboardDetail | None:
    """Update an existing dashboard."""
    records = load_dashboards_file()
    target_idx = -1
    for idx, rec in enumerate(records):
        if rec.get("id") == dashboard_id:
            target_idx = idx
            break

    if target_idx == -1:
        return None

    target = records[target_idx]
    now = datetime.now(timezone.utc).isoformat()

    if req.title is not None:
        target["title"] = req.title
    if req.description is not None:
        target["description"] = req.description
    if req.datasource_id is not None:
        target["datasource_id"] = req.datasource_id
    if req.widgets is not None:
        target["widgets"] = [w.model_dump() for w in req.widgets]

    target["updated_at"] = now
    records[target_idx] = target
    save_dashboards_file(records)

    raw_widgets = target.get("widgets", [])
    widgets: list[DashboardWidget] = []
    if isinstance(raw_widgets, list):
        for w in raw_widgets:
            if isinstance(w, dict):
                widgets.append(DashboardWidget(**w))

    return DashboardDetail(
        id=target["id"],
        title=target["title"],
        description=target.get("description"),
        datasource_id=target.get("datasource_id", "raw"),
        widgets=widgets,
        created_at=target.get("created_at", now),
        updated_at=target["updated_at"],
    )


def delete_dashboard(dashboard_id: str) -> bool:
    """Delete a dashboard by its ID. Return True if deleted, False if not found."""
    records = load_dashboards_file()
    initial_len = len(records)
    records = [r for r in records if r.get("id") != dashboard_id]
    if len(records) == initial_len:
        return False
    save_dashboards_file(records)
    return True
