from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch


TOOLKIT_ROOT = Path(__file__).resolve().parents[3]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ArcherySafeCliTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cli = load_module(
            "archery_cli_safe_test",
            TOOLKIT_ROOT / "common/entrypoints/archery_cli.py",
        )
        cls.login = load_module(
            "archery_login_private_test",
            TOOLKIT_ROOT / "skills/archery-prod-query/scripts/archery_login.py",
        )
        cls.query = load_module(
            "archery_query_private_test",
            TOOLKIT_ROOT / "skills/archery-prod-query/scripts/archery_query.py",
        )

    def test_credentials_and_session_are_secured_before_read(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            base_dir = Path(tmp_dir)
            credentials = base_dir / "archery-credentials.json"
            session = base_dir / "archery_session.json"
            credentials.write_text('{"username":"tester"}', encoding="utf-8")
            session.write_text(
                json.dumps(
                    {
                        "base_url": "https://archery.example",
                        "csrf_token": "csrf",
                        "cookie": "sessionid=value",
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(
                self.login,
                "storage_paths",
                return_value=(credentials, session),
            ), patch.object(
                self.login, "secure_private_directory"
            ) as login_secure_directory, patch.object(
                self.login, "secure_private_file"
            ) as login_secure_file:
                self.assertEqual("tester", self.login.load_creds()["username"])

            with patch.object(
                self.query,
                "storage_paths",
                return_value=(credentials, session),
            ), patch.object(
                self.query, "secure_private_directory"
            ) as query_secure_directory, patch.object(
                self.query, "secure_private_file"
            ) as query_secure_file:
                self.assertEqual(
                    "https://archery.example",
                    self.query.load_session_file()["base_url"],
                )

            self.assertEqual([call(base_dir)], login_secure_directory.call_args_list)
            self.assertEqual([call(credentials)], login_secure_file.call_args_list)
            self.assertEqual([call(base_dir)], query_secure_directory.call_args_list)
            self.assertEqual([call(session)], query_secure_file.call_args_list)

    def test_login_session_is_written_atomically_with_private_permissions(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            credentials = Path(tmp_dir) / "archery-credentials.json"
            session_path = Path(tmp_dir) / "archery_session.json"
            driver = Mock()
            driver.current_url = "https://archery.example/sqlquery/"
            driver.execute_cdp_cmd.return_value = {
                "cookies": [
                    {
                        "name": "csrftoken",
                        "value": "csrf-token",
                        "domain": "archery.example",
                    },
                    {
                        "name": "sessionid",
                        "value": "session-value",
                        "domain": "archery.example",
                    },
                ]
            }
            driver.get_cookies.return_value = []
            driver.execute_script.return_value = "agent"

            with patch.object(
                self.login,
                "storage_paths",
                return_value=(credentials, session_path),
            ), patch.object(self.login.time, "sleep"):
                session = self.login.dump_session(driver, "https://archery.example")

            self.assertEqual("csrf-token", session["csrf_token"])
            self.assertEqual(
                session,
                json.loads(session_path.read_text(encoding="utf-8")),
            )
            self.assertEqual([], list(Path(tmp_dir).glob(".archery_session.json.*.tmp")))

    def test_metadata_safe_commands_only_map_metadata_arguments(self):
        self.assertEqual(
            ["list-instances", "--format", "json"],
            self.cli.resolve_metadata_safe_args(["list-instances-safe", "--format", "json"]),
        )
        self.assertEqual(
            ["list-tables", "--format", "table", "--scope", "domestic", "--database", "ai"],
            self.cli.resolve_metadata_safe_args([
                "list-tables-safe", "--scope", "domestic", "--database", "ai",
            ]),
        )
        with self.assertRaises(SystemExit):
            self.cli.resolve_metadata_safe_args([
                "list-tables-safe", "--instance", "example-main-instance", "--database", "ai", "--sql", "select 1",
            ])

    def test_usage_describes_safe_metadata_auto_login(self):
        usage = self.cli.usage()
        self.assertIn("*-safe 元数据入口会在需要时自动登录", usage)
        self.assertNotIn("*-safe 元数据入口不会自动登录", usage)

    def test_safe_metadata_auto_logins_when_session_is_missing(self):
        runtime = {
            "python_bin": "/tmp/python",
            "agent_toolkit_root": str(TOOLKIT_ROOT),
            "archery_dir": "/tmp/archery",
        }
        with patch.object(self.cli, "runtime_and_paths", return_value=(
            runtime, Path("/tmp/login.py"), Path("/tmp/query.py")
        )), patch.object(self.cli, "require_credentials"), patch.object(
            self.cli.Path, "exists", return_value=False
        ), patch.object(
            self.cli, "run_subprocess",
            return_value=__import__("subprocess").CompletedProcess(["query"], 0, stdout="ok\n", stderr=""),
        ), patch.object(self.cli, "run_login_auto", return_value=0
        ) as login:
            code = self.cli.run_data_command(["list-instances"])

        self.assertEqual(0, code)
        login.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
