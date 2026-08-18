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
from unittest.mock import Mock, call, patch


TOOLKIT_ROOT = Path(__file__).resolve().parents[3]
TEST_TMP_ROOT = Path(tempfile.gettempdir()) / "agent-toolkit-sls-tests"


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

    def test_human_type_uses_platform_select_all_key_on_fallback(self):
        for platform, select_key in (
            ("darwin", self.login.Keys.COMMAND),
            ("win32", self.login.Keys.CONTROL),
            ("linux", self.login.Keys.CONTROL),
        ):
            with self.subTest(platform=platform):
                driver = Mock()
                driver.execute_script.side_effect = RuntimeError("use keyboard fallback")
                element = Mock()

                with patch.object(self.login.sys, "platform", platform):
                    self.login.human_type(driver, element, "demo")

                self.assertEqual(
                    [
                        call.click(),
                        call.send_keys(select_key, "a"),
                        call.send_keys(self.login.Keys.DELETE),
                        call.send_keys("demo"),
                    ],
                    element.mock_calls,
                )

    def test_credentials_and_session_are_secured_before_read(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            base_dir = Path(tmp_dir)
            credentials = base_dir / "aliyun-credentials.json"
            session = base_dir / "aliyun_session.json"
            credentials.write_text('{"username":"tester"}', encoding="utf-8")
            session.write_text('{"csrf":"token"}', encoding="utf-8")

            with patch.object(
                self.query,
                "storage_paths",
                return_value=(credentials, session),
            ), patch.object(
                self.query, "secure_private_directory"
            ) as secure_directory, patch.object(
                self.query, "secure_private_file"
            ) as secure_file:
                self.assertEqual("tester", self.query.load_creds()["username"])
                self.assertEqual("token", self.query.load_session()["csrf"])

            self.assertEqual(
                [call(base_dir), call(base_dir)],
                secure_directory.call_args_list,
            )
            self.assertEqual(
                [call(credentials), call(session)],
                secure_file.call_args_list,
            )

    def test_login_session_is_written_atomically_with_private_permissions(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            credentials = Path(tmp_dir) / "aliyun-credentials.json"
            session_path = Path(tmp_dir) / "aliyun_session.json"
            driver = Mock()
            driver.current_url = "https://sls.console.aliyun.com/lognext"
            driver.execute_cdp_cmd.return_value = {
                "cookies": [{"name": "sid", "value": "value", "domain": ".aliyun.com"}]
            }
            driver.get_cookies.return_value = []
            driver.execute_script.side_effect = lambda script: (
                "agent" if "navigator.userAgent" in script else "csrf-token"
            )

            with patch.object(
                self.login,
                "storage_paths",
                return_value=(credentials, session_path),
            ), patch.object(self.login.time, "sleep"):
                session = self.login.dump_session(driver)

            self.assertEqual("csrf-token", session["csrf"])
            self.assertEqual(
                session,
                json.loads(session_path.read_text(encoding="utf-8")),
            )
            self.assertEqual([], list(Path(tmp_dir).glob(".aliyun_session.json.*.tmp")))

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

    def test_redact_also_applies_to_output_file(self):
        response = {
            "success": True,
            "data": [
                {
                    "content": (
                        "Authorization: Bearer secret, phone=13812345678, "
                        "email=user@example.com"
                    )
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "response.json"
            with patch.object(self.query, "load_runtime_or_exit"), \
                    patch.object(self.query, "load_creds", return_value={}), \
                    patch.object(self.query, "load_session", return_value={}), \
                    patch.object(self.query, "query_logs", return_value=response), \
                    patch.object(
                        sys,
                        "argv",
                        ["aliyun_sls_query.py", "--redact", "--out", str(output)],
                    ), contextlib.redirect_stdout(io.StringIO()):
                code = self.query.main()

            written = output.read_text(encoding="utf-8")
            self.assertEqual(0, code)
            self.assertNotIn("secret", written)
            self.assertNotIn("13812345678", written)
            self.assertNotIn("user@example.com", written)

    def test_cli_relogs_and_retries_after_session_expired(self):
        expired = subprocess.CompletedProcess(
            ["query"], 2, stdout="", stderr="ConsoleNeedLogin\n"
        )
        login_ok = subprocess.CompletedProcess(["login"], 0, stdout="login ok\n", stderr="")
        query_ok = subprocess.CompletedProcess(["query"], 0, stdout="query ok\n", stderr="")
        runtime = {
            "python_bin": str(TEST_TMP_ROOT / "python"),
            "agent_toolkit_root": str(TOOLKIT_ROOT),
            "sls_dir": str(TEST_TMP_ROOT / "sls"),
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

    def test_query_logs_preserves_explicit_epoch_zero(self):
        response = unittest.mock.Mock()
        response.status_code = 200
        response.json.return_value = {"success": True, "data": []}

        with patch.object(self.query.requests, "post", return_value=response) as post:
            self.query.query_logs(
                {"cookies": {}, "csrf": "token"},
                {"project": "example-api-project", "logstore": "example-api-project"},
                from_ts=0,
                to_ts=0,
            )

        self.assertEqual(0, post.call_args.kwargs["data"]["from"])
        self.assertEqual(0, post.call_args.kwargs["data"]["to"])

    def test_search_safe_rejects_raw_routing_and_output_options(self):
        args = self.cli.resolve_search_safe_args([
            "--scope", "domestic",
            "--service", "data-center",
            "--trace-id", "6b62e9b764de5d45",
            "--minutes", "60",
            "--size", "50",
        ])
        self.assertNotIn("--project", args)
        self.assertNotIn("--logstore", args)
        self.assertNotIn("--out", args)
        self.assertNotIn("--full", args)
        with self.assertRaises(SystemExit):
            self.cli.resolve_search_safe_args([
                "--service", "ai-open-api", "--out", str(TEST_TMP_ROOT / "logs.json"),
            ])
        with self.assertRaises(SystemExit):
            self.cli.resolve_search_safe_args([
                "--service", "ai-open-api", "--minutes", "4321",
            ])

    def test_agg_safe_requires_single_select_aggregate(self):
        args = self.cli.resolve_agg_safe_args([
            "--scope", "overseas",
            "--service", "agentcc",
            "--sql", "SELECT count(*) AS c",
        ])
        self.assertIn("--sql", args)
        resolved = self.cli.resolve_agg_args(args)
        self.assertIn("--psql", resolved)
        self.assertIn("--redact", resolved)
        with self.assertRaises(SystemExit):
            self.cli.resolve_agg_safe_args([
                "--service", "ai-open-api", "--sql", "SELECT content",
            ])
        with self.assertRaises(SystemExit):
            self.cli.resolve_agg_safe_args([
                "--service", "ai-open-api", "--sql", "SELECT count(*); SELECT count(*)",
            ])

    def test_agg_safe_rejects_direct_raw_log_dimensions(self):
        for sql in (
            "SELECT content, count(*) AS c GROUP BY content",
            "SELECT message, max(__time__) GROUP BY message",
            "SELECT max(__content__) AS latest",
            'SELECT max(concat(content, "")) AS latest, count(*) AS c',
            "SELECT count(*) || content AS value",
            'SELECT regexp_extract(content, "(.*)", 1) AS raw, count(*) AS c '
            "GROUP BY raw",
        ):
            with self.subTest(sql=sql), self.assertRaises(SystemExit):
                self.cli.resolve_agg_safe_args([
                    "--service", "ai-open-api", "--sql", sql,
                ])

    def test_agg_safe_allows_derived_raw_log_dimension(self):
        sql = (
            'SELECT regexp_extract(content, "messageId[: ]*([0-9A-F]{20,})", 1) '
            "AS mid, count(*) AS c GROUP BY mid"
        )
        args = self.cli.resolve_agg_safe_args([
            "--service", "data-center", "--sql", sql,
        ])
        self.assertEqual(sql, args[args.index("--sql") + 1])

    def test_safe_queries_enable_output_redaction(self):
        args = self.cli.resolve_search_safe_args([
            "--service", "ai-open-api", "--keyword", "ERROR",
        ])
        self.assertIn("--redact", args)
        text = self.query.redact_sensitive_output(
            "Authorization: Bearer secret, phone=13812345678, email=user@example.com"
        )
        self.assertNotIn("secret", text)
        self.assertNotIn("13812345678", text)
        self.assertNotIn("user@example.com", text)

    def test_safe_query_auto_logins_when_session_is_missing(self):
        runtime = {
            "python_bin": str(TEST_TMP_ROOT / "python"),
            "agent_toolkit_root": str(TOOLKIT_ROOT),
            "sls_dir": str(TEST_TMP_ROOT / "sls"),
        }
        with patch.object(
            self.cli, "runtime_and_paths", return_value=(
                runtime, TEST_TMP_ROOT / "login.py", TEST_TMP_ROOT / "query.py"
            )
        ), patch.object(self.cli, "require_credentials"), patch.object(
            self.cli.Path, "exists", return_value=False
        ), patch.object(
            self.cli, "run_subprocess",
            return_value=subprocess.CompletedProcess(["query"], 0, stdout="ok\n", stderr=""),
        ), patch.object(self.cli, "run_login_auto", return_value=0
        ) as login:
            code = self.cli.run_query([
                "--service", "ai-open-api", "--redact",
            ])

        self.assertEqual(0, code)
        login.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
