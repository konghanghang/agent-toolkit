from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "common"))
import work_config

SPEC = importlib.util.spec_from_file_location(
    "init_work_config_test", ROOT / "scripts" / "init_work_config.py"
)
initializer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(initializer)


class WorkConfigTest(unittest.TestCase):
    def test_config_path_override_and_live_reload(self):
        with tempfile.TemporaryDirectory() as temporary:
            configured = Path(temporary) / "config.json"
            explicit = Path(temporary) / "explicit.json"
            configured.write_text('{"schema_version": 1, "value": "first"}')
            explicit.write_text('{"schema_version": 1, "value": "explicit"}')
            with patch.dict(os.environ, {work_config.CONFIG_ENV: str(configured)}):
                self.assertEqual("first", work_config.load_work_config()["value"])
                configured.write_text('{"schema_version": 1, "value": "updated"}')
                self.assertEqual("updated", work_config.load_work_config()["value"])
                self.assertEqual(
                    "explicit", work_config.load_work_config(explicit)["value"]
                )

    def test_missing_and_invalid_configs_do_not_create_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            self.assertEqual({}, work_config.load_work_config(path, required=False))
            with self.assertRaises(work_config.WorkConfigError):
                work_config.load_work_config(path)
            self.assertFalse(path.exists())
            for payload in ("not-json", "[]", '{"schema_version": true}', '{"schema_version": 2}'):
                path.write_text(payload)
                with self.subTest(payload=payload), self.assertRaises(
                    work_config.WorkConfigError
                ):
                    work_config.load_work_config(path, required=False)

    def test_initializer_dry_run_preserves_existing_and_creates_private_template(self):
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(
            io.StringIO()
        ):
            path = Path(temporary) / "work" / "config.json"
            self.assertTrue(initializer.initialize_config(path, dry_run=True))
            self.assertFalse(path.parent.exists())
            self.assertTrue(initializer.initialize_config(path))
            data = json.loads(path.read_text())
            self.assertEqual([], data["tapd_weekly_report"]["members"])
            if os.name != "nt":
                self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))
                self.assertEqual(0o700, stat.S_IMODE(path.parent.stat().st_mode))
            path.write_text('{"private": "keep"}')
            self.assertFalse(initializer.initialize_config(path))
            self.assertEqual('{"private": "keep"}', path.read_text())


if __name__ == "__main__":
    unittest.main()
