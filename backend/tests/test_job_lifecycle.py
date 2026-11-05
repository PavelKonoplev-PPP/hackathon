import time
import unittest
from unittest.mock import patch

import httpx
import openai
import psycopg
from fastapi.testclient import TestClient

try:
    from backend.app.api.schemas import ChatResponse
    from backend.app.main import app
    from backend.app.services import agent, query_tracker as qt
except ImportError:
    from app.api.schemas import ChatResponse
    from app.main import app
    from app.services import agent, query_tracker as qt

REQUEST = httpx.Request("POST", "http://localhost:11434/v1/chat/completions")
SECRET = "password=secret host=10.0.0.5"


class JobStateTests(unittest.TestCase):
    def setUp(self):
        qt._JOBS.clear()
        self.addCleanup(qt._JOBS.clear)

    def test_final_state_never_changes(self):
        job = qt.create_job("q", "s")
        qt.cancel_job(job.job_id)
        qt.finish_job(job.job_id, ChatResponse(answer="поздно"))
        qt.fail_job(job.job_id, "поздно")
        qt.update_job_step(job.job_id, "x", "поздно")
        st = qt.get_job(job.job_id)
        self.assertEqual(st.status, "cancelled")
        self.assertIsNone(st.result)
        self.assertIsNone(st.error)

    def test_cancel_after_completion_keeps_result(self):
        job = qt.create_job("q", "s")
        qt.finish_job(job.job_id, ChatResponse(answer="готово"))
        self.assertTrue(qt.cancel_job(job.job_id))
        self.assertEqual(qt.get_job(job.job_id).status, "completed")

    def test_job_past_deadline_is_failed_without_worker(self):
        job = qt.create_job("q", "s", timeout_s=-1)
        st = qt.get_job(job.job_id)
        self.assertEqual(st.status, "failed")
        self.assertEqual(st.error, qt.TIMEOUT_MESSAGE)
        qt.finish_job(job.job_id, ChatResponse(answer="поздно"))
        self.assertIsNone(qt.get_job(job.job_id).result)

    def test_recorder_stops_cancelled_job(self):
        job = qt.create_job("q", "s")
        recorder = qt.StepRecorder(job_id=job.job_id)
        recorder.step("a", "Шаг")
        qt.cancel_job(job.job_id)
        with self.assertRaises(qt.JobStopped):
            recorder.step("b", "Следующий шаг")

    def test_stop_passes_through_agent_except_exception(self):
        # агент ловит Exception, чтобы повторить SQL: остановка не должна попасть в эти повторы
        self.assertFalse(issubclass(qt.JobStopped, Exception))

    def test_worker_stops_between_steps(self):
        job = qt.create_job("q", "s")
        steps = []

        def fake_agent(message, session_id, datasource_id, table_name, recorder):
            recorder.step("one", "Первый")
            steps.append("one")
            qt.cancel_job(job.job_id)
            recorder.step("two", "Второй")
            steps.append("two")
            return {"answer": "не должно дойти"}

        with patch.object(agent, "process_chat_message", side_effect=fake_agent):
            qt.execute_job_worker(job.job_id)
        self.assertEqual(steps, ["one"])
        st = qt.get_job(job.job_id)
        self.assertEqual(st.status, "cancelled")
        self.assertIsNone(st.result)

    def test_worker_reports_safe_error(self):
        job = qt.create_job("q", "s")
        with patch.object(agent, "process_chat_message", side_effect=agent.AgentServiceError(SECRET)):
            qt.execute_job_worker(job.job_id)
        st = qt.get_job(job.job_id)
        self.assertEqual(st.status, "failed")
        self.assertNotIn("secret", st.error)
        self.assertNotIn("secret", str(st.steps))

    def test_jobs_do_not_mix(self):
        a = qt.create_job("a", "s1")
        b = qt.create_job("b", "s2")
        qt.finish_job(a.job_id, ChatResponse(answer="ответ a"))
        qt.cancel_job(b.job_id)
        self.assertEqual(qt.get_job(a.job_id).result.answer, "ответ a")
        self.assertIsNone(qt.get_job(b.job_id).result)


class CleanupTests(unittest.TestCase):
    def setUp(self):
        qt._JOBS.clear()
        self.addCleanup(qt._JOBS.clear)

    def test_old_finished_jobs_are_forgotten(self):
        old = qt.create_job("old", "s")
        qt.finish_job(old.job_id, ChatResponse(answer="x"))
        qt._JOBS[old.job_id]["_finished_at"] = time.monotonic() - qt.FINISHED_JOB_TTL_S - 1
        running = qt.create_job("running", "s")
        self.assertIsNone(qt.get_job(old.job_id))
        self.assertIsNotNone(qt.get_job(running.job_id))

    def test_limit_evicts_finished_not_running(self):
        running = qt.create_job("running", "s")
        with patch.object(qt, "MAX_TRACKED_JOBS", 3):
            finished = qt.create_job("done", "s")
            qt.finish_job(finished.job_id, ChatResponse(answer="x"))
            qt.create_job("c", "s")
            qt.create_job("d", "s")
        self.assertIsNotNone(qt.get_job(running.job_id))
        self.assertIsNone(qt.get_job(finished.job_id))


class ErrorMessageTests(unittest.TestCase):
    def wrapped(self, cause):
        try:
            try:
                raise cause
            except Exception as exc:
                raise agent.AgentServiceError(f"Ошибка при ответе на вопрос: {exc} {SECRET}") from exc
        except agent.AgentServiceError as exc:
            return exc

    def test_model_down(self):
        exc = self.wrapped(openai.APIConnectionError(request=REQUEST))
        self.assertEqual(qt.user_error_message(exc), qt.MODEL_DOWN_MESSAGE)

    def test_model_timeout(self):
        exc = self.wrapped(openai.APITimeoutError(request=REQUEST))
        self.assertEqual(qt.user_error_message(exc), qt.MODEL_TIMEOUT_MESSAGE)

    def test_database_down(self):
        exc = self.wrapped(psycopg.OperationalError("connection refused"))
        self.assertEqual(qt.user_error_message(exc), qt.DB_DOWN_MESSAGE)

    def test_database_timeout(self):
        exc = self.wrapped(psycopg.errors.QueryCanceled("canceling statement"))
        self.assertEqual(qt.user_error_message(exc), qt.DB_TIMEOUT_MESSAGE)

    def test_unknown_error_hides_details(self):
        message = qt.user_error_message(RuntimeError(SECRET))
        self.assertEqual(message, qt.GENERIC_MESSAGE)
        self.assertNotIn("secret", message)


class ApiTests(unittest.TestCase):
    def test_chat_endpoint_hides_details(self):
        with patch.object(agent, "process_chat_message", side_effect=RuntimeError(SECRET)), TestClient(app) as client:
            r = client.post("/api/chat", json={"session_id": "s", "message": "вопрос"})
        self.assertEqual(r.status_code, 500)
        self.assertNotIn("secret", r.text)

    def test_cancelled_job_returns_no_result(self):
        job = qt.create_job("q", "s")
        with TestClient(app) as client:
            client.post(f"/api/chat/jobs/{job.job_id}/cancel")
            st = client.get(f"/api/chat/jobs/{job.job_id}").json()
        self.assertEqual(st["status"], "cancelled")
        self.assertIsNone(st["result"])


if __name__ == "__main__":
    unittest.main()
