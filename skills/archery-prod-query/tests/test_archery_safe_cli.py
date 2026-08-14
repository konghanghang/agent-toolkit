from __future__ import annotations

import importlib.util
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


class ArcherySafeCliTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cli = load_module(
            "archery_cli_safe_test",
            TOOLKIT_ROOT / "common/entrypoints/archery_cli.py",
        )

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
