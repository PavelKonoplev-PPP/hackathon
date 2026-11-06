"""Unittest suite for Issue #122: Task management, cancel tokens and timeouts."""
import sys
import unittest
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.services.query_tracker import (
    create_job,
    get_job,
    cancel_job,
    is_job_cancelled,
    finish_job,
    fail_job,
    list_jobs,
    JOB_TIMEOUT_S,
    user_error_message,
    JobStopped,
)
from backend.app.api.schemas import ChatResponse


class TestJobLifecycle(unittest.TestCase):
    """Issue #122: Task management & cancellation."""

    def test_job_cancel_sets_status_cancelled(self):
        job = create_job(message="Тестовый вопрос", session_id="s1")
        self.assertEqual(job.status, "pending")
        self.assertFalse(is_job_cancelled(job.job_id))

        # Cancel active job
        ok = cancel_job(job.job_id)
        self.assertTrue(ok)
        self.assertTrue(is_job_cancelled(job.job_id))

        updated = get_job(job.job_id)
        self.assertIsNotNone(updated)
        self.assertEqual(updated.status, "cancelled")
        self.assertEqual(updated.current_step, "Остановлено")

    def test_cancel_nonexistent_job_returns_false(self):
        ok = cancel_job("non-existent-id")
        self.assertFalse(ok)

    def test_concurrent_jobs_isolation(self):
        job1 = create_job(message="Запрос 1", session_id="session_A")
        job2 = create_job(message="Запрос 2", session_id="session_B")

        cancel_job(job1.job_id)
        self.assertTrue(is_job_cancelled(job1.job_id))
        self.assertFalse(is_job_cancelled(job2.job_id))

        self.assertEqual(get_job(job1.job_id).status, "cancelled")
        self.assertEqual(get_job(job2.job_id).status, "pending")

    def test_job_timeout_constant_defined(self):
        self.assertGreaterEqual(JOB_TIMEOUT_S, 60)

    def test_user_error_message_masks_internals(self):
        exc = Exception("Connection refused to postgres://user:secret@10.0.0.1:5432/db")
        msg = user_error_message(exc)
        self.assertNotIn("secret", msg)
        self.assertNotIn("10.0.0.1", msg)
        self.assertNotIn("postgres", msg)


if __name__ == "__main__":
    unittest.main()
