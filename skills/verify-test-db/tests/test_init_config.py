from __future__ import annotations

import importlib.util
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path


TOOLKIT_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = TOOLKIT_ROOT / "skills" / "verify-test-db" / "scripts" / "init_config.py"


def load_module():
    spec = importlib.util.spec_from_file_location("verify_test_db_init_config_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


init_config = load_module()


class InitConfigTest(unittest.TestCase):
    def test_creates_exact_private_template(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir) / "test-db"

            message, success = init_config.initialize_profile(base_dir)

            profile_file = base_dir / "profiles.json"
            self.assertTrue(success)
            self.assertIn("CREATE", message)
            self.assertEqual(
                init_config.PROFILE_TEMPLATE,
                json.loads(profile_file.read_text(encoding="utf-8")),
            )
            if os.name != "nt":
                self.assertEqual(0o700, stat.S_IMODE(base_dir.stat().st_mode))
                self.assertEqual(0o600, stat.S_IMODE(profile_file.stat().st_mode))

    def test_dry_run_does_not_create_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir) / "test-db"

            message, success = init_config.initialize_profile(base_dir, dry_run=True)

            self.assertTrue(success)
            self.assertIn("CREATE", message)
            self.assertFalse(base_dir.exists())

    def test_existing_profile_is_preserved_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            profile_file = base_dir / "profiles.json"
            original = b'{"profiles":{"custom":{"host":"real"}}}\n'
            profile_file.write_bytes(original)
            if os.name != "nt":
                profile_file.chmod(0o600)

            message, success = init_config.initialize_profile(base_dir)

            self.assertTrue(success)
            self.assertIn("preserved", message)
            self.assertEqual(original, profile_file.read_bytes())

    def test_runtime_test_db_dir_is_used_when_present(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            configured = root / "custom-test-db"
            runtime_file = root / "runtime.json"
            runtime_file.write_text(
                json.dumps({"test_db_dir": str(configured)}),
                encoding="utf-8",
            )

            resolved = init_config.resolve_test_db_dir(runtime_file=runtime_file)

            self.assertEqual(configured.resolve(), resolved)

    @unittest.skipIf(os.name == "nt", "symlink setup differs on Windows")
    def test_existing_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            base_dir = root / "test-db"
            base_dir.mkdir()
            target = root / "target.json"
            target.write_text("{}", encoding="utf-8")
            (base_dir / "profiles.json").symlink_to(target)

            message, success = init_config.initialize_profile(base_dir)

            self.assertFalse(success)
            self.assertIn("not a regular file", message)


if __name__ == "__main__":
    unittest.main()
