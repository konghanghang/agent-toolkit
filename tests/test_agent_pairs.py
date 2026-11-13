from __future__ import annotations

import fnmatch
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
GUARD_PATH = ROOT / "claude" / "agents" / "hooks" / "bash_guard.py"
GUARD_SPEC = importlib.util.spec_from_file_location("agent_toolkit_bash_guard", GUARD_PATH)
assert GUARD_SPEC is not None and GUARD_SPEC.loader is not None
guard = importlib.util.module_from_spec(GUARD_SPEC)
GUARD_SPEC.loader.exec_module(guard)
GIT_INSPECTION = (
    "git status", "git status --short --branch", "git diff",
    "git diff --cached -- source.py", "git log -5 --oneline",
    "git show HEAD:source.py", "git rev-parse HEAD",
)
GIT_DELIVERY = (
    "git add source.py", "git commit -m change", "git push origin master",
    "git switch -c other", "git checkout other", "git clean -fd",
    "git reset --hard", "git merge other", "git rebase master",
    "git stash", "git worktree add other", "git cherry-pick HEAD",
    "git revert HEAD",
)
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
        cls.claude = {}
        for path in (ROOT / "claude" / "agents").glob("*.md"):
            _, frontmatter, instructions = path.read_text(encoding="utf-8").split("---", 2)
            cls.claude[path.stem] = (yaml.safe_load(frontmatter), instructions.strip())

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

    def test_claude_agents_share_the_contract_without_fixed_model(self) -> None:
        self.assertEqual(set(self.codex), set(self.claude))
        for name, codex in self.codex.items():
            claude, instructions = self.claude[name]
            with self.subTest(agent=name):
                self.assertEqual(name, claude["name"])
                self.assertEqual(codex["description"], claude["description"])
                self.assertEqual(codex["developer_instructions"].strip(), instructions)
                # The parent agent picks a model per task; roles never pin one.
                self.assertFalse({"model", "effort", "permissionMode"} & set(claude))

    def test_claude_roles_limit_tools_and_guard_bash(self) -> None:
        for name, (claude, _) in self.claude.items():
            with self.subTest(agent=name):
                hooks = claude["hooks"]["PreToolUse"]
                self.assertEqual(["Bash"], [hook["matcher"] for hook in hooks])
                command = hooks[0]["hooks"][0]["command"]
                mode = "read-only" if name in READ_ONLY else "no-delivery"
                self.assertIn(f"bash_guard.py\" {mode}", command)
                self.assertTrue(command.endswith("|| exit 2"), "guard failures must block")
                if name in READ_ONLY:
                    tools = {tool.strip() for tool in claude["tools"].split(",")}
                    self.assertEqual({"Read", "Grep", "Glob", "Bash"}, tools)
                    self.assertNotIn("disallowedTools", claude)
                else:
                    self.assertNotIn("tools", claude)
                    disallowed = {tool.strip() for tool in claude["disallowedTools"].split(",")}
                    self.assertIn("Agent", disallowed)
                    edits = {"Edit", "Write", "NotebookEdit"}
                    if name == "verifier":
                        self.assertTrue(edits <= disallowed)
                    else:
                        self.assertFalse(edits & disallowed)

    def test_claude_guard_matches_git_boundaries(self) -> None:
        for command in GIT_INSPECTION:
            with self.subTest(allowed=command):
                self.assertIsNone(guard.check_read_only(command))
                self.assertIsNone(guard.check_no_delivery(command))
        for command in GIT_DELIVERY + (
            "git -C repo commit -m x", "git -c user.name=x commit -m x",
            "FOO=1 git push", "env git add .", "/usr/bin/git reset --hard",
            "npm test && git commit -m x", "(git stash)",
        ):
            with self.subTest(denied=command):
                self.assertIsNotNone(guard.check_read_only(command))
                self.assertIsNotNone(guard.check_no_delivery(command))

    def test_claude_guard_read_only_rejects_writes_and_other_commands(self) -> None:
        for command in (
            "python -m unittest", "rm source.py", "git diff --output=changes.patch",
            "git show HEAD > changes.txt", "git status; rm x", "git log | tee out",
            "git diff $(rm x)", "git status `rm x`", "ls",
        ):
            with self.subTest(command=command):
                self.assertIsNotNone(guard.check_read_only(command))
        for command in ("python -m unittest", "pytest -q", "mvn test", "npm test", "git status && npm test"):
            with self.subTest(execution=command):
                self.assertIsNone(guard.check_no_delivery(command))

    def test_claude_guard_hook_protocol_fails_closed(self) -> None:
        python = shutil.which("python3") or sys.executable

        def run(mode: str, payload: str) -> int:
            return subprocess.run(
                [python, str(GUARD_PATH), mode], input=payload, text=True,
                capture_output=True,
            ).returncode

        allowed = json.dumps({"tool_name": "Bash", "tool_input": {"command": "git status"}})
        denied = json.dumps({"tool_name": "Bash", "tool_input": {"command": "git push"}})
        self.assertEqual(0, run("read-only", allowed))
        self.assertEqual(2, run("read-only", denied))
        self.assertEqual(2, run("no-delivery", denied))
        self.assertEqual(2, run("read-only", "not json"))
        self.assertEqual(2, run("unknown-mode", allowed))

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
        allowed = GIT_INSPECTION
        denied = GIT_DELIVERY
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
