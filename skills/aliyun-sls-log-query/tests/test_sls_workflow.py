from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


TOOLKIT_ROOT = Path(__file__).resolve().parents[3]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class SlsWorkflowTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.login = load_module(
            "aliyun_login_test",
            TOOLKIT_ROOT / "skills/aliyun-sls-log-query/scripts/aliyun_login.py",
        )
        cls.query = load_module(
            "aliyun_sls_query_test",
            TOOLKIT_ROOT / "skills/aliyun-sls-log-query/scripts/aliyun_sls_query.py",
        )
        cls.cli = load_module(
            "sls_cli_test",
            TOOLKIT_ROOT / "common/entrypoints/sls_cli.py",
        )

    def test_login_closes_browser_by_default(self):
        self.assertFalse(self.login.parse_args([]).keep)
        self.assertTrue(self.login.parse_args(["--keep"]).keep)

    def test_query_returns_nonzero_for_expired_session(self):
        response = {
            "success": False,
            "code": "ConsoleNeedLogin",
            "message": "needLogin",
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "response.json"
            with patch.object(self.query, "load_runtime_or_exit"), \
                    patch.object(self.query, "load_creds", return_value={}), \
                    patch.object(self.query, "load_session", return_value={}), \
                    patch.object(self.query, "query_logs", return_value=response), \
                    patch.object(sys, "argv", ["aliyun_sls_query.py", "--out", str(output)]), \
                    contextlib.redirect_stderr(io.StringIO()) as stderr:
                code = self.query.main()

            self.assertEqual(2, code)
            self.assertIn("ConsoleNeedLogin", stderr.getvalue())
            self.assertEqual(
                response,
                json.loads(output.read_text(encoding="utf-8")),
            )

    def test_cli_relogs_and_retries_after_session_expired(self):
        expired = subprocess.CompletedProcess(
            ["query"], 2, stdout="", stderr="ConsoleNeedLogin\n"
        )
        login_ok = subprocess.CompletedProcess(["login"], 0, stdout="login ok\n", stderr="")
        query_ok = subprocess.CompletedProcess(["query"], 0, stdout="query ok\n", stderr="")
        runtime = {
            "python_bin": "/tmp/python",
            "agent_toolkit_root": str(TOOLKIT_ROOT),
            "sls_dir": "/tmp/sls",
        }

        with patch.object(self.cli, "runtime_and_paths", return_value=(
                runtime,
                TOOLKIT_ROOT / "skills/aliyun-sls-log-query/scripts/aliyun_login.py",
                TOOLKIT_ROOT / "skills/aliyun-sls-log-query/scripts/aliyun_sls_query.py",
            )), patch.object(self.cli, "require_credentials"), \
                patch.object(self.cli.Path, "exists", return_value=True), \
                patch.object(self.cli, "run_subprocess", side_effect=[expired, login_ok, query_ok]) as run:
            code = self.cli.run_query(["--scope", "overseas", "--service", "company"])

        self.assertEqual(0, code)
        self.assertEqual("--no-keep", run.call_args_list[1].args[0][-1])
        self.assertEqual(3, run.call_count)


if __name__ == "__main__":
    unittest.main()
