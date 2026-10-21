from __future__ import annotations

import fnmatch
import tomllib
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
READ_ONLY = {"evidence_explorer", "reviewer", "design_challenger"}
EXECUTION = {"implementer", "verifier"}


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
        self.assertEqual(READ_ONLY | EXECUTION, set(self.codex))
        self.assertEqual(set(self.codex), set(self.opencode))
        for name, codex in self.codex.items():
            with self.subTest(agent=name):
                opencode, instructions = self.opencode[name]
                self.assertEqual(name, codex["name"])
                self.assertEqual(codex["description"], opencode["description"])
                self.assertTrue(instructions)
                self.assertEqual(codex["developer_instructions"].strip(), instructions)
                if "model" in codex:
                    model = opencode["model"].split("/", 1)[1]
                    effort = codex.get("model_reasoning_effort")
                    self.assertEqual(codex["model"] + (f"#{effort}" if effort else ""), model)
                else:
                    self.assertNotIn("model", opencode)
                    self.assertNotIn("model_reasoning_effort", codex)

    def test_codex_agents_do_not_request_broader_parent_permissions(self) -> None:
        for name, codex in self.codex.items():
            with self.subTest(agent=name):
                self.assertTrue({"name", "description", "developer_instructions"} <= set(codex))
                self.assertFalse({"approval_policy", "model_provider", "mcp_servers"} & set(codex))
                if name in READ_ONLY:
                    self.assertEqual("read-only", codex.get("sandbox_mode"))
                else:
                    self.assertNotIn("sandbox_mode", codex)

    def test_all_roles_keep_parent_coordination_and_read_tools(self) -> None:
        for name, (opencode, _) in self.opencode.items():
            with self.subTest(agent=name):
                self.assertEqual("subagent", opencode["mode"])
                self.assertNotIn("steps", opencode)
                self.assertEqual("deny", self.effect(opencode, "subagent", "worker"))
                for action in ("read", "glob", "grep"):
                    self.assertFalse(any(
                        rule["action"] in (action, "*") and rule["effect"] == "deny"
                        for rule in opencode["permissions"]
                    ))

    def test_read_only_roles_limit_direct_edits_and_shell_commands(self) -> None:
        for name in READ_ONLY:
            opencode, _ = self.opencode[name]
            with self.subTest(agent=name):
                self.assertEqual("deny", self.effect(opencode, "edit", "source.py", "allow"))
                for command in ("python -m unittest", "rm source.py", "git diff --output=changes.patch", "git show HEAD > changes.txt"):
                    self.assertEqual("deny", self.effect(opencode, "shell", command, "allow"))

    def test_execution_roles_respect_global_runtime_defaults_without_extra_allows(self) -> None:
        for name in EXECUTION:
            opencode, _ = self.opencode[name]
            with self.subTest(agent=name):
                for inherited in ("allow", "ask", "deny"):
                    for command in ("python -m unittest", "pytest -q", "mvn test", "npm test"):
                        self.assertEqual(inherited, self.effect(opencode, "shell", command, inherited))
                self.assertFalse(any(rule["action"] in ("shell", "edit") and rule["effect"] == "allow" for rule in opencode["permissions"]))
        implementer, _ = self.opencode["implementer"]
        verifier, _ = self.opencode["verifier"]
        for inherited in ("allow", "ask", "deny"):
            self.assertEqual(inherited, self.effect(implementer, "edit", "assigned.py", inherited))
            self.assertEqual("deny", self.effect(verifier, "edit", "source.py", inherited))

    def test_git_inspection_is_available_and_direct_git_delivery_is_denied(self) -> None:
        allowed = (
            "git status", "git status --short --branch", "git diff",
            "git diff --cached -- source.py", "git log -5 --oneline",
            "git show HEAD:source.py", "git rev-parse HEAD",
        )
        denied = (
            "git add source.py", "git commit -m change", "git push origin master",
            "git switch -c other", "git checkout other", "git clean -fd",
            "git reset --hard", "git merge other", "git rebase master",
            "git stash", "git worktree add other", "git cherry-pick HEAD",
            "git revert HEAD",
        )
        for name, (opencode, _) in self.opencode.items():
            for command in allowed:
                with self.subTest(agent=name, allowed=command):
                    self.assertEqual("allow", self.effect(opencode, "shell", command, "allow"))
            for command in denied:
                with self.subTest(agent=name, denied=command):
                    self.assertEqual("deny", self.effect(opencode, "shell", command, "allow"))

    @staticmethod
    def effect(agent: dict, action: str, resource: str, inherited: str | None = None) -> str | None:
        matched = [
            rule["effect"] for rule in agent["permissions"]
            if fnmatch.fnmatchcase(action, rule["action"])
            and fnmatch.fnmatchcase(resource, rule["resource"])
        ]
        return matched[-1] if matched else inherited


if __name__ == "__main__":
    unittest.main()
