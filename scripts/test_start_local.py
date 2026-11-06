"""Non-destructive startup safety regressions (issue #123)."""
import io
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.start_local import (
    preflight, python_bin, require_free_port,
    terminate_child, verify_local_model,
)


class SafeStartupTests(unittest.TestCase):
    def test_existing_service_port_is_not_reused_or_killed(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            sock.listen(1)
            port = sock.getsockname()[1]
            with self.assertRaisesRegex(RuntimeError, "уже занят"):
                require_free_port(port)

    def test_required_credentials_are_diagnosed_without_mutating(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("scripts.start_local.ROOT", root):
                with self.assertRaisesRegex(RuntimeError, "POSTGRES_READER_USER"):
                    preflight()
            self.assertEqual(list(root.iterdir()), [])

    def test_existing_ollama_model_is_accepted(self):
        data = {"models": [{"name": "qwen-bi:latest"}]}
        with patch("scripts.start_local.urllib.request.urlopen",
                   return_value=io.BytesIO(json.dumps(data).encode("utf-8"))):
            verify_local_model({"LLM_BASE_URL": "http://localhost:11434/v1",
                                "LLM_MODEL": "qwen-bi"})

    def test_missing_ollama_model_refused_without_download(self):
        data = {"models": [{"name": "qwen2.5:3b"}]}
        with patch("scripts.start_local.urllib.request.urlopen",
                   return_value=io.BytesIO(json.dumps(data).encode("utf-8"))):
            with self.assertRaisesRegex(RuntimeError, "отсутствует"):
                verify_local_model({"LLM_BASE_URL": "http://localhost:11434/v1",
                                    "LLM_MODEL": "qwen-bi"})

    def test_remote_provider_does_not_use_ollama_api(self):
        with patch("scripts.start_local.urllib.request.urlopen") as get:
            verify_local_model({"LLM_BASE_URL": "https://models.example/v1",
                                "LLM_MODEL": "remote-model"})
            get.assert_not_called()

    def test_configured_python_path_is_not_overridden(self):
        with patch.dict("os.environ", {"BI_PYTHON": "/opt/bi-env/bin/python"}):
            self.assertEqual(python_bin(), "/opt/bi-env/bin/python")

    def test_cleanup_never_touches_unowned_processes(self):
        terminate_child(None)


if __name__ == "__main__":
    unittest.main()
