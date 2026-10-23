from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "archery_routing_test", ROOT / "common" / "entrypoints" / "archery_cli.py"
)
archery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(archery)


class ArcheryRoutingTest(unittest.TestCase):
    def test_scope_and_module_targets_reload_from_private_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            config = {
                "schema_version": 1,
                "archery": {
                    "scopes": {"domestic": "example-domestic", "overseas": "example-overseas"},
                    "modules": {"example-module": "example-module-db"},
                },
            }
            path.write_text(json.dumps(config))
            with patch.dict(os.environ, {"AGENT_TOOLKIT_WORK_CONFIG": str(path)}), contextlib.redirect_stderr(io.StringIO()):
                cases = [
                    (["--scope", "domestic"], "example-domestic"),
                    (["--scope", "overseas"], "example-overseas"),
                    (["--module", "example-module"], "example-module-db"),
                    (["--scope", "domestic", "--module", "example-module"], "example-module-db"),
                    (["--scope", "overseas", "--module", "example-module"], "example-overseas"),
                ]
                for arguments, expected in cases:
                    with self.subTest(arguments=arguments):
                        result = archery.resolve_instance_args(["list-databases", *arguments])
                        self.assertEqual(expected, result[result.index("--instance") + 1])
                config["archery"]["scopes"]["domestic"] = "example-updated"
                path.write_text(json.dumps(config))
                result = archery.resolve_instance_args(["list-databases", "--scope", "domestic"])
                self.assertEqual("example-updated", result[-1])

    def test_explicit_instance_and_instance_listing_do_not_read_route_config(self):
        with patch.object(archery, "load_work_config", side_effect=AssertionError("must not read config")):
            self.assertEqual(
                ["query", "--instance", "example-explicit", "--sql", "select 1"],
                archery.resolve_instance_args(["query", "--instance", "example-explicit", "--sql", "select 1"]),
            )
            self.assertEqual(["list-instances"], archery.resolve_instance_args(["list-instances"]))

    def test_missing_route_stops_before_credentials_login_or_query(self):
        runtime = {"python_bin": sys.executable, "archery_dir": "unused"}
        with patch.object(archery, "load_work_config", return_value={"schema_version": 1}), patch.object(
            archery, "runtime_and_paths", return_value=(runtime, Path("login.py"), Path("query.py"))
        ), patch.object(archery, "require_credentials") as credentials, patch.object(
            archery, "run_login_auto"
        ) as login, patch.object(archery, "run_subprocess") as query:
            with self.assertRaisesRegex(SystemExit, "archery"):
                archery.run_data_command(["list-databases", "--scope", "domestic"])
            credentials.assert_not_called()
            login.assert_not_called()
            query.assert_not_called()

        with patch.object(
            archery, "load_work_config",
            return_value={"schema_version": 1, "archery": {"scopes": {"domestic": ""}}},
        ), self.assertRaisesRegex(SystemExit, "非空实例配置"):
            archery.resolve_instance_args(["list-databases", "--scope", "domestic"])


if __name__ == "__main__":
    unittest.main()
