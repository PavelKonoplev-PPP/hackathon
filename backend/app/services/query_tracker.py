"""Query execution status tracking and asynchronous job runner (BI-07 / Issue #35, #122).

Tracks lifecycle stages, progress percentages, latency metrics, and execution steps
for analytical queries (both synchronous chat requests and background jobs).

Job states: pending -> running -> completed | failed | cancelled. pending can also go straight to
a final state. A final state never changes: a late result of a stopped or timed-out job is dropped.

Stopping works between agent steps. A model call already in progress cannot be interrupted, so
the worker may run for up to LLM_TIMEOUT more seconds, and a SQL statement for up to 20 s. A job
past its deadline is reported as failed right away, without waiting for the worker.
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
FINAL_STATES = ("completed", "failed", "cancelled")
JOB_TIMEOUT_S = 180.0
DASHBOARD_JOB_TIMEOUT_S = 420.0
FINISHED_JOB_TTL_S = 3600.0

TIMEOUT_MESSAGE = "Запрос выполнялся слишком долго и был остановлен. Попробуйте сформулировать его проще или уже."
MODEL_TIMEOUT_MESSAGE = "Модель не ответила вовремя. Повторите запрос через минуту или сформулируйте его проще."
MODEL_DOWN_MESSAGE = "Модель сейчас недоступна. Проверьте, что она запущена, и повторите запрос."
DB_DOWN_MESSAGE = "База данных сейчас недоступна. Проверьте подключение и повторите запрос."
DB_TIMEOUT_MESSAGE = "Запрос к базе выполнялся слишком долго. Уточните период или условия и повторите."
GENERIC_MESSAGE = "Не удалось выполнить запрос. Попробуйте ещё раз."

_LOCK = threading.Lock()
_JOBS: OrderedDict[str, dict[str, Any]] = OrderedDict()


class JobStopped(BaseException):
    """The job was cancelled or ran out of time.

    Not an Exception: the agent catches Exception to retry SQL, and must not retry a stopped job.
    """


def user_error_message(exc: BaseException) -> str:
    """Text for the user. Never the raw exception: it may contain hosts, logins and SQL."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        name = type(current).__name__
        module = type(current).__module__
        if module.startswith(("openai", "httpx")):
            return MODEL_TIMEOUT_MESSAGE if "Timeout" in name else MODEL_DOWN_MESSAGE
        if module.startswith("psycopg"):
            return DB_TIMEOUT_MESSAGE if name == "QueryCanceled" else DB_DOWN_MESSAGE
        if name == "LLMError":
            return MODEL_DOWN_MESSAGE
        if name == "AgentServiceError" and str(current).startswith("Не удалось подключиться"):
            return DB_DOWN_MESSAGE
        current = current.__cause__ or current.__context__
    return GENERIC_MESSAGE


class StepRecorder:
    """Helper to record execution steps with timing during query execution."""

    def __init__(self, job_id: str | None = None, check: Callable[[], None] | None = None) -> None:
        self.job_id = job_id
        self.check = check
        self.steps: list[ExecutionStep] = []
        self._start_time = time.perf_counter()
        self._current_step_code: str | None = None
        self._current_step_title: str | None = None
        self._current_step_start: float = self._start_time

    def step(self, code: str, title: str, details: str | None = None) -> None:
        """Mark start of a new step, automatically closing previous step."""
        if self.check:
            self.check()
        if self.job_id:
            raise_if_stopped(self.job_id)
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
    timeout_s: float = JOB_TIMEOUT_S,
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
        "_deadline": time.monotonic() + timeout_s,
    }

    with _LOCK:
        _prune_jobs()
        _JOBS[job_id] = job_data

    return JobStatusResponse(**{k: v for k, v in job_data.items() if not k.startswith("_")})


def _prune_jobs() -> None:
    """Forget old finished jobs. Call with _LOCK held. Running jobs are never evicted."""
    now = time.monotonic()
    for job_id, job in list(_JOBS.items()):
        if job["status"] in FINAL_STATES and now - job.get("_finished_at", now) > FINISHED_JOB_TTL_S:
            del _JOBS[job_id]
    while len(_JOBS) >= MAX_TRACKED_JOBS:
        finished = next((k for k, j in _JOBS.items() if j["status"] in FINAL_STATES), None)
        if finished is None:
            break
        del _JOBS[finished]


def _set_final(job: dict[str, Any], status: str, step: str) -> None:
    """Move a job to a final state. Call with _LOCK held."""
    job["status"] = status
    job["current_step"] = step
    job["updated_at"] = datetime.now(timezone.utc).isoformat()
    job["execution_time_ms"] = round((time.perf_counter() - job.get("_start_time", time.perf_counter())) * 1000, 2)
    job["_finished_at"] = time.monotonic()


def _expire(job: dict[str, Any]) -> None:
    """Report a job past its deadline as failed. Call with _LOCK held."""
    if job["status"] not in FINAL_STATES and time.monotonic() > job.get("_deadline", float("inf")):
        _set_final(job, "failed", "Превышено время выполнения")
        job["error"] = TIMEOUT_MESSAGE


def raise_if_stopped(job_id: str) -> None:
    """Called between agent steps: stop working on a cancelled or timed-out job."""
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return
        _expire(job)
        if job["status"] in FINAL_STATES:
            raise JobStopped(job_id)


def get_job(job_id: str) -> JobStatusResponse | None:
    """Retrieve status and details of a tracked job."""
    with _LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return None
        _expire(job)
        return JobStatusResponse(**{k: v for k, v in job.items() if not k.startswith("_")})


def list_jobs(limit: int = 50) -> list[JobStatusResponse]:
    """List recent tracked jobs."""
    with _LOCK:
        items = list(_JOBS.values())
        for item in items:
            _expire(item)
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
        if not job or job["status"] in FINAL_STATES:
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


def set_job_progress(job_id: str, pct: int) -> None:
    """Set an explicit progress percentage for jobs outside the standard pipeline."""
    with _LOCK:
        job = _JOBS.get(job_id)
        if job and job["status"] not in FINAL_STATES:
            job["progress_pct"] = max(0, min(99, pct))


def cancel_job(job_id: str) -> bool:
    """Ask a running job to stop. Returns False when the job is unknown."""
    with _LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return False
        if job["status"] not in FINAL_STATES:
            _set_final(job, "cancelled", "Остановлено")
        return True


def is_job_cancelled(job_id: str) -> bool:
    with _LOCK:
        job = _JOBS.get(job_id)
        return bool(job and job["status"] == "cancelled")


def finish_job(job_id: str, result: ChatResponse) -> None:
    """Mark a job as successfully completed with final ChatResponse."""
    with _LOCK:
        job = _JOBS.get(job_id)
        if not job or job["status"] in FINAL_STATES:
            return

        now_iso = datetime.now(timezone.utc).isoformat()
        _set_final(job, "completed", "Завершено")
        job["progress_pct"] = 100

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
        if not job or job["status"] in FINAL_STATES:
            return

        now_iso = datetime.now(timezone.utc).isoformat()
        _set_final(job, "failed", "Ошибка выполнения")
        job["error"] = error_message
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
    except JobStopped:
        logger.info("Query job %s stopped", job_id)
    except Exception as exc:
        logger.exception("Error executing async query job %s: %s", job_id, exc)
        recorder.finish(final_status="failed")
        fail_job(job_id, user_error_message(exc))


def execute_dashboard_job_worker(job_id: str) -> None:
    """Build a dashboard from a text request in a background thread (Issue #117)."""
    with _LOCK:
        job = _JOBS.get(job_id)
    if not job:
        return

    from .dashboard_builder import build_dashboard

    recorder = StepRecorder(job_id=job_id)

    def on_step(title: str, pct: int) -> None:
        recorder.step("dashboard", title)
        set_job_progress(job_id, pct)

    try:
        result = build_dashboard(
            job["message"],
            datasource_id=job["datasource_id"],
            table_name=job["table_name"],
            on_step=on_step,
            is_cancelled=lambda: is_job_cancelled(job_id),
            check=lambda: raise_if_stopped(job_id),
        )
        recorder.finish(final_status="completed")
        finish_job(job_id, ChatResponse(**{
            **result,
            "steps": recorder.steps,
            "execution_time_ms": recorder.total_duration_ms,
        }))
    except JobStopped:
        logger.info("Dashboard job %s stopped", job_id)
    except Exception as exc:
        logger.exception("Error building dashboard in job %s: %s", job_id, exc)
        recorder.finish(final_status="failed")
        fail_job(job_id, user_error_message(exc))
