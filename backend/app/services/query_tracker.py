"""Query execution status tracking and asynchronous job runner (BI-07 / Issue #35).

Tracks lifecycle stages, progress percentages, latency metrics, and execution steps
for analytical queries (both synchronous chat requests and background jobs).
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Callable

from ..api.schemas import ChatResponse, ExecutionStep, JobStatusResponse

logger = logging.getLogger(__name__)

# Standard analytical pipeline stages
STANDARD_STAGES = [
    ("parse_intent", "Анализ намерения и безопасности"),
    ("select_table", "Выбор таблицы и сопоставление схемы"),
    ("generate_sql", "Генерация аналитического SQL"),
    ("validate_sql", "Проверка синтаксиса и безопасности SQL"),
    ("execute_sql", "Выполнение запроса в базе данных"),
    ("synthesize", "Формирование ответа и визуализации"),
]

MAX_TRACKED_JOBS = 500

_LOCK = threading.Lock()
_JOBS: OrderedDict[str, dict[str, Any]] = OrderedDict()


class StepRecorder:
    """Helper to record execution steps with timing during query execution."""

    def __init__(self, job_id: str | None = None) -> None:
        self.job_id = job_id
        self.steps: list[ExecutionStep] = []
        self._start_time = time.perf_counter()
        self._current_step_code: str | None = None
        self._current_step_title: str | None = None
        self._current_step_start: float = self._start_time

    def step(self, code: str, title: str, details: str | None = None) -> None:
        """Mark start of a new step, automatically closing previous step."""
        now = time.perf_counter()
        now_iso = datetime.now(timezone.utc).isoformat()

        # Close previous step
        if self._current_step_code is not None:
            duration = round((now - self._current_step_start) * 1000, 2)
            self.steps.append(
                ExecutionStep(
                    step=self._current_step_code,
                    title=self._current_step_title or self._current_step_code,
                    status="completed",
                    duration_ms=duration,
                    timestamp=now_iso,
                )
            )

        self._current_step_code = code
        self._current_step_title = title
        self._current_step_start = now

        if self.job_id:
            update_job_step(self.job_id, code, title, status="in_progress", details=details)

    def finish(self, final_status: str = "completed") -> list[ExecutionStep]:
        """Close final step and return complete steps list."""
        now = time.perf_counter()
        now_iso = datetime.now(timezone.utc).isoformat()

        if self._current_step_code is not None:
            duration = round((now - self._current_step_start) * 1000, 2)
            self.steps.append(
                ExecutionStep(
                    step=self._current_step_code,
                    title=self._current_step_title or self._current_step_code,
                    status=final_status if final_status in ("completed", "failed", "skipped") else "completed",
                    duration_ms=duration,
                    timestamp=now_iso,
                )
            )
            self._current_step_code = None

        return self.steps

    @property
    def total_duration_ms(self) -> float:
        return round((time.perf_counter() - self._start_time) * 1000, 2)


def create_job(
    message: str,
    session_id: str | None = None,
    datasource_id: str | None = "raw",
    table_name: str | None = None,
) -> JobStatusResponse:
    """Register a new query execution job."""
    job_id = uuid.uuid4().hex[:12]
    now_iso = datetime.now(timezone.utc).isoformat()

    job_data: dict[str, Any] = {
        "job_id": job_id,
        "session_id": session_id,
        "message": message,
        "datasource_id": datasource_id or "raw",
        "table_name": table_name,
        "status": "pending",
        "current_step": "Принят в обработку",
        "progress_pct": 5,
        "steps": [
            ExecutionStep(
                step="queued",
                title="Принят в очередь обработки",
                status="completed",
                duration_ms=0.5,
                timestamp=now_iso,
            )
        ],
        "result": None,
        "error": None,
        "created_at": now_iso,
        "updated_at": now_iso,
        "execution_time_ms": None,
        "_start_time": time.perf_counter(),
    }

    with _LOCK:
        if len(_JOBS) >= MAX_TRACKED_JOBS:
            _JOBS.popitem(last=False)
        _JOBS[job_id] = job_data

    return JobStatusResponse(**{k: v for k, v in job_data.items() if not k.startswith("_")})


def get_job(job_id: str) -> JobStatusResponse | None:
    """Retrieve status and details of a tracked job."""
    with _LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return None
        return JobStatusResponse(**{k: v for k, v in job.items() if not k.startswith("_")})


def list_jobs(limit: int = 50) -> list[JobStatusResponse]:
    """List recent tracked jobs."""
    with _LOCK:
        items = list(_JOBS.values())
    items.sort(key=lambda j: j.get("created_at", ""), reverse=True)
    return [
        JobStatusResponse(**{k: v for k, v in item.items() if not k.startswith("_")})
        for item in items[:limit]
    ]


def update_job_step(
    job_id: str,
    step: str,
    title: str,
    status: str = "in_progress",
    details: str | None = None,
) -> None:
    """Update execution progress for a specific job."""
    with _LOCK:
        job = _JOBS.get(job_id)
        if not job or job["status"] in ("completed", "failed"):
            return

        now_iso = datetime.now(timezone.utc).isoformat()
        job["status"] = "running"
        job["current_step"] = title
        job["updated_at"] = now_iso

        # Approximate percentage based on standard stages
        stage_names = [s[0] for s in STANDARD_STAGES]
        if step in stage_names:
            idx = stage_names.index(step)
            job["progress_pct"] = min(95, int(15 + (idx + 1) * (80 / len(stage_names))))

        existing_steps = job["steps"]
        step_obj = ExecutionStep(
            step=step,
            title=title,
            status=status if status in ("pending", "in_progress", "completed", "failed", "skipped") else "in_progress",
            details=details,
            timestamp=now_iso,
        )
        existing_steps.append(step_obj)


def finish_job(job_id: str, result: ChatResponse) -> None:
    """Mark a job as successfully completed with final ChatResponse."""
    with _LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return

        now_iso = datetime.now(timezone.utc).isoformat()
        start = job.get("_start_time", time.perf_counter())
        total_ms = round((time.perf_counter() - start) * 1000, 2)

        job["status"] = "completed"
        job["current_step"] = "Завершено"
        job["progress_pct"] = 100
        job["updated_at"] = now_iso
        job["execution_time_ms"] = total_ms

        # Sync steps from result if richer
        if result.steps:
            job["steps"] = result.steps
        else:
            job["steps"].append(
                ExecutionStep(
                    step="finished",
                    title="Формирование ответа завершено",
                    status="completed",
                    duration_ms=1.0,
                    timestamp=now_iso,
                )
            )

        job["result"] = result


def fail_job(job_id: str, error_message: str) -> None:
    """Mark a job as failed with error details."""
    with _LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return

        now_iso = datetime.now(timezone.utc).isoformat()
        start = job.get("_start_time", time.perf_counter())
        total_ms = round((time.perf_counter() - start) * 1000, 2)

        job["status"] = "failed"
        job["current_step"] = "Ошибка выполнения"
        job["updated_at"] = now_iso
        job["error"] = error_message
        job["execution_time_ms"] = total_ms
        job["steps"].append(
            ExecutionStep(
                step="error",
                title="Ошибка при выполнении запроса",
                status="failed",
                details=error_message,
                timestamp=now_iso,
            )
        )


def execute_job_worker(job_id: str) -> None:
    """Worker function to process job in background thread."""
    with _LOCK:
        job = _JOBS.get(job_id)
    if not job:
        return

    from .agent import process_chat_message

    recorder = StepRecorder(job_id=job_id)
    try:
        recorder.step("parse_intent", "Анализ вопроса и проверка ограничений")
        result_dict = process_chat_message(
            message=job["message"],
            session_id=job["session_id"],
            datasource_id=job["datasource_id"],
            table_name=job["table_name"],
            recorder=recorder,
        )
        recorder.finish(final_status="completed")
        # агент уже кладёт status/steps/execution_time_ms в результат — дополняем, а не дублируем
        chat_resp = ChatResponse(**{
            **result_dict,
            "status": result_dict.get("status") or "completed",
            "steps": recorder.steps,
            "execution_time_ms": recorder.total_duration_ms,
        })
        finish_job(job_id, chat_resp)
    except Exception as exc:
        logger.exception("Error executing async query job %s: %s", job_id, exc)
        recorder.finish(final_status="failed")
        fail_job(job_id, str(exc))
