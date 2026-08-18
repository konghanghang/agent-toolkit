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
            expected_test_db_dir = config_dir / "test-db"

            with patch.object(runtime, "CONFIG_FILE", config_file), patch.object(
                runtime, "CONFIG_DIR", config_dir
            ):
                loaded = runtime.load_runtime(COMMON_DIR / "runtime.py")

            self.assertEqual(str(expected_jms_dir.resolve()), loaded["jms_dir"])
            self.assertFalse(expected_jms_dir.exists())
            self.assertEqual(str(expected_test_db_dir.resolve()), loaded["test_db_dir"])
            self.assertFalse(expected_test_db_dir.exists())
            self.assertNotIn("prometheus_base_url", loaded)

    def test_loading_runtime_normalizes_separate_prometheus_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_dir = root / "config"
            config_dir.mkdir()
            paths = {
                key: root / key
                for key in ("work_root", "tmp_dir", "sls_dir", "archery_dir")
            }
            for path in paths.values():
                path.mkdir()
            python_bin = root / "python"
            python_bin.touch()
            config_file = config_dir / "runtime.json"
            config_file.write_text(
                json.dumps(
                    {
                        **{key: str(path) for key, path in paths.items()},
                        "agent_toolkit_root": str(ROOT),
                        "python_bin": str(python_bin),
                        "prometheus_base_url": "https://monitor.example/prometheus/",
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(runtime, "CONFIG_FILE", config_file), patch.object(
                runtime, "CONFIG_DIR", config_dir
            ):
                loaded = runtime.load_runtime(COMMON_DIR / "runtime.py")

            self.assertEqual(
                "https://monitor.example/prometheus",
                loaded["prometheus_base_url"],
            )

    def test_prometheus_url_rejects_credentials(self) -> None:
        with self.assertRaisesRegex(runtime.RuntimeConfigError, "用户名或密码"):
            runtime.normalize_http_base_url(
                "https://user:secret@monitor.example/prometheus",
                "prometheus_base_url",
            )

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

    def test_bootstrap_prompts_until_prometheus_url_is_valid(self) -> None:
        with patch(
            "builtins.input",
            side_effect=["not-a-url", "https://monitor.example/prometheus/"],
        ), patch("builtins.print"):
            configured = bootstrap.prompt_url("Prometheus 地址")

        self.assertEqual("https://monitor.example/prometheus", configured)

    def test_bootstrap_installs_project_requirements(self) -> None:
        requirements = ROOT / "requirements.txt"
        dependency_names = {
            line.strip().lower()
            for line in requirements.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        self.assertIn("cryptography", dependency_names)
        self.assertIn("pyyaml", dependency_names)
        self.assertIn("tzdata", dependency_names)
        self.assertIn("pymysql", dependency_names)
        self.assertIn("sqlglot", dependency_names)

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

    def test_bootstrap_has_cross_platform_testdb_templates(self) -> None:
        template_dir = ROOT / "common" / "templates" / "bin"

        for name in ("testdb", "testdb.cmd", "testdb.ps1"):
            with self.subTest(name=name):
                self.assertTrue((template_dir / name).is_file())

    def test_bootstrap_initializes_profile_in_selected_test_db_directory(self) -> None:
        python_bin = ROOT / ".venv" / "bin" / "python"
        test_db_dir = Path("/tmp/agent-toolkit-test-db")
        initializer = ROOT / "skills" / "verify-test-db" / "scripts" / "init_config.py"

        with patch.object(bootstrap.subprocess, "run") as run:
            bootstrap.initialize_test_db_profile(ROOT, python_bin, test_db_dir)

        run.assert_called_once_with(
            [
                str(python_bin),
                str(initializer),
                "--base-dir",
                str(test_db_dir),
            ],
            check=True,
        )


if __name__ == "__main__":
    unittest.main()
