from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
COMMON_DIR = ROOT / "common"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


runtime = load_module("agent_toolkit_runtime_test", COMMON_DIR / "runtime.py")
sys.path.insert(0, str(COMMON_DIR))
try:
    bootstrap = load_module(
        "agent_toolkit_bootstrap_test", COMMON_DIR / "bootstrap_work.py"
    )
finally:
    sys.path.pop(0)


class RuntimeTest(unittest.TestCase):
    def test_loading_existing_runtime_does_not_create_jms_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_dir = root / "config"
            config_dir.mkdir()
            work_root = root / "work"
            tmp_dir = root / "tmp"
            sls_dir = root / "sls"
            archery_dir = root / "archery"
            for directory in (work_root, tmp_dir, sls_dir, archery_dir):
                directory.mkdir()
            python_bin = root / "python"
            python_bin.touch()
            config_file = config_dir / "runtime.json"
            config_file.write_text(
                json.dumps(
                    {
                        "work_root": str(work_root),
                        "agent_toolkit_root": str(ROOT),
                        "python_bin": str(python_bin),
                        "tmp_dir": str(tmp_dir),
                        "sls_dir": str(sls_dir),
                        "archery_dir": str(archery_dir),
                    }
                ),
                encoding="utf-8",
            )
            expected_jms_dir = config_dir / "jms"

            with patch.object(runtime, "CONFIG_FILE", config_file), patch.object(
                runtime, "CONFIG_DIR", config_dir
            ):
                loaded = runtime.load_runtime(COMMON_DIR / "runtime.py")

            self.assertEqual(str(expected_jms_dir.resolve()), loaded["jms_dir"])
            self.assertFalse(expected_jms_dir.exists())

    def test_bootstrap_defaults_to_project_virtual_environment(self) -> None:
        expected = (
            ROOT / ".venv" / "Scripts" / "python.exe"
            if os.name == "nt"
            else ROOT / ".venv" / "bin" / "python"
        )
        self.assertEqual(expected, bootstrap.platform_default_python(ROOT))

    def test_bootstrap_preserves_virtual_environment_python_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            base_python = root / "base-python"
            base_python.touch()
            venv_python = root / ".venv" / "bin" / "python"
            venv_python.parent.mkdir(parents=True)
            venv_python.symlink_to(base_python)

            configured = bootstrap.configured_python_path(venv_python)

            self.assertEqual(venv_python.absolute(), configured)
            self.assertNotEqual(base_python.resolve(), configured)

    def test_bootstrap_installs_project_requirements(self) -> None:
        requirements = ROOT / "requirements.txt"
        dependency_names = {
            line.strip().lower()
            for line in requirements.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        self.assertIn("cryptography", dependency_names)
        self.assertIn("pyyaml", dependency_names)

        python_bin = ROOT / ".venv" / "bin" / "python"
        with patch.object(bootstrap, "prompt_yes_no", return_value=True), patch.object(
            bootstrap.subprocess, "run"
        ) as run:
            bootstrap.install_dependencies(python_bin, requirements)

        run.assert_called_once_with(
            [
                str(python_bin),
                "-m",
                "pip",
                "install",
                "-r",
                str(requirements),
            ],
            check=True,
        )


if __name__ == "__main__":
    unittest.main()
