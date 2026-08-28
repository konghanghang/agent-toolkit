from __future__ import annotations

import importlib.util
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path


TOOLKIT_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = TOOLKIT_ROOT / "skills" / "gitlab-run-pipeline" / "scripts" / "init_config.py"


def load_module():
    spec = importlib.util.spec_from_file_location("gitlab_init_config_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


init_config = load_module()


class InitConfigTest(unittest.TestCase):
    def test_creates_exact_private_empty_template(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir) / "gitlab"

            message, success = init_config.initialize_credentials(base_dir)

            credentials_file = base_dir / "gitlab-credentials.json"
            self.assertTrue(success)
            self.assertIn("CREATE", message)
            self.assertEqual(
                {"base_url": "", "private_token": ""},
                json.loads(credentials_file.read_text(encoding="utf-8")),
            )
            if os.name != "nt":
                self.assertEqual(0o700, stat.S_IMODE(base_dir.stat().st_mode))
                self.assertEqual(0o600, stat.S_IMODE(credentials_file.stat().st_mode))

    def test_dry_run_does_not_create_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir) / "gitlab"

            message, success = init_config.initialize_credentials(base_dir, dry_run=True)

            self.assertTrue(success)
            self.assertIn("CREATE", message)
            self.assertFalse(base_dir.exists())

    def test_existing_credentials_are_preserved_byte_for_byte(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            credentials_file = base_dir / "gitlab-credentials.json"
            original = b'{"base_url":"https://gitlab.example.test","private_token":"secret"}\n'
            credentials_file.write_bytes(original)
            if os.name != "nt":
                credentials_file.chmod(0o644)

            message, success = init_config.initialize_credentials(base_dir)

            self.assertTrue(success)
            self.assertIn("preserved", message)
            self.assertEqual(original, credentials_file.read_bytes())
            if os.name != "nt":
                self.assertEqual(0o600, stat.S_IMODE(credentials_file.stat().st_mode))

    def test_runtime_gitlab_dir_is_used_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            configured = root / "custom-gitlab"
            runtime_file = root / "runtime.json"
            runtime_file.write_text(
                json.dumps({"gitlab_dir": str(configured)}),
                encoding="utf-8",
            )

            resolved = init_config.resolve_gitlab_dir(runtime_file=runtime_file)

            self.assertEqual(configured.resolve(), resolved)

    @unittest.skipIf(os.name == "nt", "symlink setup differs on Windows")
    def test_existing_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            base_dir = root / "gitlab"
            base_dir.mkdir()
            target = root / "target.json"
            target.write_text("{}", encoding="utf-8")
            (base_dir / "gitlab-credentials.json").symlink_to(target)

            message, success = init_config.initialize_credentials(base_dir)

            self.assertFalse(success)
            self.assertIn("not a regular file", message)


if __name__ == "__main__":
    unittest.main()
