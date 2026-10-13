from __future__ import annotations

import fnmatch
import tomllib
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class AgentPairsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.codex = {
            path.stem: tomllib.loads(path.read_text(encoding="utf-8"))
            for path in (ROOT / "codex" / "agents").glob("*.toml")
        }
        cls.opencode = {}
        for path in (ROOT / "opencode" / "agents").glob("*.md"):
            _, frontmatter, instructions = path.read_text(encoding="utf-8").split("---", 2)
            cls.opencode[path.stem] = (yaml.safe_load(frontmatter), instructions.strip())

    def test_pairs_share_the_task_contract_and_model_role(self) -> None:
        self.assertTrue(self.codex)
        self.assertEqual(set(self.codex), set(self.opencode))
        for name, codex in self.codex.items():
            with self.subTest(agent=name):
                opencode, instructions = self.opencode[name]
                self.assertEqual(name, codex["name"])
                self.assertEqual(codex["description"], opencode["description"])
                # Shared Chinese task instructions carry the responsibility and
                # output contract; each platform keeps its own configuration.
                self.assertEqual(codex["developer_instructions"].strip(), instructions)
                model = opencode["model"].split("/", 1)[1]
                self.assertEqual(
                    f'{codex["model"]}#{codex["model_reasoning_effort"]}', model
                )

    def test_pairs_keep_read_only_boundaries_without_a_one_sided_step_limit(self) -> None:
        for name, codex in self.codex.items():
            with self.subTest(agent=name):
                opencode, _ = self.opencode[name]
                self.assertEqual("read-only", codex["sandbox_mode"])
                self.assertEqual("subagent", opencode["mode"])
                self.assertNotIn("steps", opencode)
                self.assertEqual("deny", self.effect(opencode, "edit", "source.py"))
                self.assertEqual("deny", self.effect(opencode, "subagent", "worker"))
                # Native read/search tools inherit the platform's file policy;
                # agent overrides must not disable these task capabilities.
                for action in ("read", "glob", "grep"):
                    self.assertFalse(any(
                        rule["action"] in (action, "*") and rule["effect"] == "deny"
                        for rule in opencode["permissions"]
                    ))

    def test_git_inspection_is_allowed_and_file_writes_are_denied(self) -> None:
        allowed = (
            "git status", "git status --short --branch", "git diff",
            "git diff --cached -- source.py", "git log -5 --oneline",
            "git show HEAD:source.py", "git rev-parse HEAD",
        )
        denied = (
            "git diff --output=changes.patch", "git show HEAD > changes.txt",
            "git log --output history.txt", "git add source.py",
            "git commit -m change", "git push origin master", "rm source.py",
        )
        for name, (opencode, _) in self.opencode.items():
            for command in allowed:
                with self.subTest(agent=name, allowed=command):
                    self.assertEqual("allow", self.effect(opencode, "shell", command))
            for command in denied:
                with self.subTest(agent=name, denied=command):
                    self.assertEqual("deny", self.effect(opencode, "shell", command))

    @staticmethod
    def effect(agent: dict, action: str, resource: str) -> str | None:
        matched = [
            rule["effect"] for rule in agent["permissions"]
            if fnmatch.fnmatchcase(action, rule["action"])
            and fnmatch.fnmatchcase(resource, rule["resource"])
        ]
        return matched[-1] if matched else None


if __name__ == "__main__":
    unittest.main()
