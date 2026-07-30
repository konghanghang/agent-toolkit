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

    def test_agg_rejects_unknown_argument(self):
        with self.assertRaises(SystemExit) as raised:
            self.cli.resolve_agg_args([
                "--service", "data-center",
                "--sql", "SELECT count(*) as c",
                "--minuts", "5",
            ])

        self.assertEqual(2, raised.exception.code)

    def test_agg_infers_logstore_from_explicit_project(self):
        args = self.cli.resolve_agg_args([
            "--project", "example-api-project",
            "--sql", "SELECT count(*) as c",
        ])

        self.assertEqual("example-api-project", args[args.index("--project") + 1])
        self.assertEqual("example-api-project", args[args.index("--logstore") + 1])

    def test_agg_requires_logstore_for_unknown_project(self):
        with self.assertRaisesRegex(SystemExit, "请补充 --logstore"):
            self.cli.resolve_agg_args([
                "--project", "custom-project",
                "--sql", "SELECT count(*) as c",
            ])

    def test_agg_rejects_size_above_sls_limit(self):
        with self.assertRaisesRegex(SystemExit, "--size 必须在 1-100"):
            self.cli.resolve_agg_args([
                "--service", "data-center",
                "--sql", "SELECT content",
                "--size", "200",
            ])

    def test_query_logs_enables_psql_in_request(self):
        response = unittest.mock.Mock()
        response.status_code = 200
        response.json.return_value = {"success": True, "data": [{"c": "1"}]}

        with patch.object(self.query.requests, "post", return_value=response) as post:
            self.query.query_logs(
                {"cookies": {}, "csrf": "token"},
                {"project": "example-api-project", "logstore": "example-api-project"},
                query="ai-open-api | SELECT count(*) as c",
                psql=True,
            )

        self.assertEqual("true", post.call_args.kwargs["data"]["pSql"])


if __name__ == "__main__":
    unittest.main()
