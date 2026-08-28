from __future__ import annotations

import os
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = ROOT / "skills" / "git-commit" / "SKILL.md"


def fenced_blocks(content: str, language: str) -> list[str]:
    return re.findall(
        rf"```{re.escape(language)}\n(.*?)```",
        content,
        flags=re.DOTALL,
    )


class GitCommitSkillCrossPlatformTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.content = SKILL_PATH.read_text(encoding="utf-8")

    def test_upstream_revisions_are_quoted_for_powershell(self) -> None:
        command_blocks = "\n".join(
            fenced_blocks(self.content, "bash")
            + fenced_blocks(self.content, "powershell")
        )
        upstream_lines = [
            line for line in command_blocks.splitlines() if "@{upstream}" in line
        ]
        self.assertEqual(6, len(upstream_lines))
        for line in upstream_lines:
            with self.subTest(line=line):
                self.assertRegex(
                    line,
                    r"'@\{upstream\}(?:\.\.\.?HEAD)?'",
                )

    def test_divergence_is_checked_before_commit_and_push(self) -> None:
        command_blocks = "\n".join(
            fenced_blocks(self.content, "bash")
            + fenced_blocks(self.content, "powershell")
        )
        self.assertEqual(
            2,
            command_blocks.count(
                "git rev-list --left-right --count '@{upstream}...HEAD'"
            ),
        )

    def test_multiline_commit_has_shell_specific_continuations(self) -> None:
        bash_blocks = [
            block
            for block in fenced_blocks(self.content, "bash")
            if "git commit -S" in block
        ]
        powershell_blocks = [
            block
            for block in fenced_blocks(self.content, "powershell")
            if "git commit -S" in block
        ]
        self.assertEqual(1, len(bash_blocks))
        self.assertEqual(1, len(powershell_blocks))
        self.assertIn("git commit -S \\\n", bash_blocks[0])
        self.assertIn("git commit -S `\n", powershell_blocks[0])
        self.assertNotRegex(powershell_blocks[0], r"(?m)\\$")

    def test_powershell_commit_body_is_one_argument_with_real_newlines(self) -> None:
        powershell_block = next(
            block
            for block in fenced_blocks(self.content, "powershell")
            if "git commit -S" in block
        )
        self.assertIn("  -m '- 新增本地 AccessToken 接口", powershell_block)
        self.assertIn("\n- 保留上游响应状态", powershell_block)
        self.assertIn("\n- 保持现有业务鉴权行为不变'", powershell_block)
        self.assertNotIn(r"\n", powershell_block)
        self.assertNotIn("$CommitBody", powershell_block)

    @unittest.skipUnless(os.name == "nt", "需要 Windows PowerShell")
    def test_documented_commands_execute_in_windows_powershell(self) -> None:
        powershell_blocks = fenced_blocks(self.content, "powershell")
        upstream_commands = "\n".join(
            line
            for block in powershell_blocks
            for line in block.splitlines()
            if line.startswith("git ") and "@{upstream}" in line
        )
        commit_block = next(
            block for block in powershell_blocks if "git commit -S" in block
        )
        script = r"""
$ErrorActionPreference = 'Stop'
$script:Calls = @()
function git {
    $script:Calls += ,([object[]]$args)
}

__UPSTREAM_COMMANDS__

$ExpectedRevisions = @(
    '@{upstream}',
    '@{upstream}...HEAD',
    '@{upstream}',
    '@{upstream}..HEAD',
    '@{upstream}...HEAD',
    '@{upstream}..HEAD'
)
$ActualRevisions = @()
foreach ($Call in $script:Calls) {
    $RevisionArgs = @(
        $Call | Where-Object { ([string]$_).StartsWith('@{upstream}') }
    )
    if ($RevisionArgs.Count -ne 1) {
        throw "Expected one upstream revision argument, got $($RevisionArgs.Count)"
    }
    $ActualRevisions += [string]$RevisionArgs[0]
}
if ($script:Calls.Count -ne $ExpectedRevisions.Count) {
    throw "Expected $($ExpectedRevisions.Count) upstream calls, got $($script:Calls.Count)"
}
for ($Index = 0; $Index -lt $ExpectedRevisions.Count; $Index++) {
    if ($ActualRevisions[$Index] -ne $ExpectedRevisions[$Index]) {
        throw "Unexpected revision argument: $($ActualRevisions[$Index])"
    }
}

$script:Calls = @()
__COMMIT_COMMAND__

if ($script:Calls.Count -ne 1 -or $script:Calls[0].Count -ne 6) {
    throw "The commit example did not produce one six-argument git call"
}
$BodyLines = ([string]$script:Calls[0][5]) -split "\r?\n"
if ($BodyLines.Count -ne 3) {
    throw "Expected a three-line commit body, got $($BodyLines.Count) lines"
}
foreach ($Line in $BodyLines) {
    if (-not $Line.StartsWith('- ')) {
        throw "Commit body line is not a list item: $Line"
    }
}
"""
        script = script.replace("__UPSTREAM_COMMANDS__", upstream_commands)
        script = script.replace("__COMMIT_COMMAND__", commit_block.rstrip())
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            0,
            result.returncode,
            msg=f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
