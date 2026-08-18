from __future__ import annotations

import argparse
import importlib.util
import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_DIR / "scripts" / "resolve_iminling_repo.py"


def load_module():
    spec = importlib.util.spec_from_file_location("resolve_iminling_repo_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


resolver = load_module()


def create_repository(root: Path) -> Path:
    for relative in resolver.REQUIRED_PATHS:
        path = root / relative
        if Path(relative).suffix:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture\n", encoding="utf-8")
        else:
            path.mkdir(parents=True, exist_ok=True)
    return root


class ResolveIminlingRepoTest(unittest.TestCase):
    def test_validate_requires_the_complete_repository_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.assertIsNone(resolver.validate(root))

            repository = create_repository(root)
            self.assertEqual(repository.resolve(), resolver.validate(repository))

            (repository / "hugo.yaml").unlink()
            self.assertIsNone(resolver.validate(repository))

    def test_git_config_path_returns_only_a_successful_nonempty_value(self) -> None:
        completed = subprocess.CompletedProcess(
            ["git"],
            0,
            stdout=" configured-repository \n",
            stderr="",
        )
        with patch.object(resolver.subprocess, "run", return_value=completed) as run:
            self.assertEqual("configured-repository", resolver.git_config_path())

        run.assert_called_once_with(
            ["git", "config", "--global", "--get", "iminling.repoPath"],
            check=False,
            capture_output=True,
            text=True,
        )

        for result in (
            subprocess.CompletedProcess(["git"], 1, stdout="ignored\n", stderr=""),
            subprocess.CompletedProcess(["git"], 0, stdout=" \n", stderr=""),
        ):
            with self.subTest(returncode=result.returncode, stdout=result.stdout):
                with patch.object(resolver.subprocess, "run", return_value=result):
                    self.assertIsNone(resolver.git_config_path())

    def test_explicit_path_has_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = create_repository(Path(temp_dir) / "explicit")
            output = io.StringIO()
            with patch.object(
                resolver,
                "parse_args",
                return_value=argparse.Namespace(repo=str(repository)),
            ), patch.dict(
                resolver.os.environ,
                {"IMINLING_PAGES_REPO": "/ignored/environment"},
                clear=True,
            ), patch.object(
                resolver,
                "git_config_path",
                return_value="/ignored/git",
            ), patch.object(
                resolver,
                "cwd_candidates",
                side_effect=AssertionError("cwd fallback must not run"),
            ), redirect_stdout(output):
                code = resolver.main()

        self.assertEqual(0, code)
        self.assertEqual(str(repository.resolve()), output.getvalue().strip())

    def test_invalid_configured_path_fails_without_falling_back(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            valid_fallback = create_repository(Path(temp_dir) / "fallback")
            error = io.StringIO()
            with patch.object(
                resolver,
                "parse_args",
                return_value=argparse.Namespace(
                    repo="definitely-missing-explicit-path"
                ),
            ), patch.dict(
                resolver.os.environ,
                {},
                clear=True,
            ), patch.object(
                resolver,
                "git_config_path",
                return_value=None,
            ), patch.object(
                resolver,
                "cwd_candidates",
                return_value=[valid_fallback],
            ) as candidates, redirect_stderr(error):
                code = resolver.main()

        self.assertEqual(2, code)
        self.assertIn("Configured iminling-pages path is invalid", error.getvalue())
        candidates.assert_not_called()

    def test_cwd_fallback_and_not_found_exit_codes_are_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = create_repository(Path(temp_dir) / "from-cwd")
            common_patches = (
                patch.object(
                    resolver,
                    "parse_args",
                    return_value=argparse.Namespace(repo=None),
                ),
                patch.dict(resolver.os.environ, {}, clear=True),
                patch.object(resolver, "git_config_path", return_value=None),
            )
            output = io.StringIO()
            with common_patches[0], common_patches[1], common_patches[2], patch.object(
                resolver,
                "cwd_candidates",
                return_value=[Path(temp_dir) / "missing", repository],
            ), redirect_stdout(output):
                self.assertEqual(0, resolver.main())
            self.assertEqual(str(repository.resolve()), output.getvalue().strip())

            error = io.StringIO()
            with patch.object(
                resolver,
                "parse_args",
                return_value=argparse.Namespace(repo=None),
            ), patch.dict(
                resolver.os.environ,
                {},
                clear=True,
            ), patch.object(
                resolver,
                "git_config_path",
                return_value=None,
            ), patch.object(
                resolver,
                "cwd_candidates",
                return_value=[Path(temp_dir) / "missing"],
            ), redirect_stderr(error):
                self.assertEqual(1, resolver.main())
            self.assertIn("Unable to locate iminling-pages", error.getvalue())


if __name__ == "__main__":
    unittest.main()
