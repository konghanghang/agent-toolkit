#!/usr/bin/env python3
"""PreToolUse guard for Agent Toolkit Claude Code subagents.

Claude Code can only enable or remove the whole Bash tool for a subagent, so
this hook enforces the same command boundaries as the paired Codex/OpenCode
roles:

- read-only: only plain Git inspection commands, no redirects or chaining.
- no-delivery: any command, except Git staging, history or worktree changes.

Exit code 0 allows the command; exit code 2 blocks it and reports the reason
to the agent. The check reads command text, so it is a role boundary, not a
system sandbox.
"""

from __future__ import annotations

import json
import re
import shlex
import sys

READ_ONLY_GIT = {"status", "diff", "log", "show", "rev-parse"}
DELIVERY_GIT = {
    "add", "commit", "push", "merge", "rebase", "reset", "clean", "checkout",
    "switch", "stash", "cherry-pick", "revert", "worktree",
}
# Git global options that take a separate value, e.g. `git -C repo commit`.
GIT_OPTIONS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}
CHAIN_TOKENS = ("\n", "\r", ";", "&", "|", "`", "$(", "<", ">")
SEGMENT_SPLIT = re.compile(r"\|\||&&|[;|&\n\r]")
WRAPPERS = {"command", "env", "exec", "nohup", "time", "xargs", "sudo"}


def git_subcommand(argv: list[str]) -> str | None:
    """Return the Git subcommand of argv, skipping wrappers and global options."""
    index = 0
    while index < len(argv) and (argv[index] in WRAPPERS or "=" in argv[index]):
        index += 1
    if index >= len(argv) or argv[index].rsplit("/", 1)[-1] != "git":
        return None
    index += 1
    while index < len(argv) and argv[index].startswith("-"):
        option = argv[index]
        index += 2 if option in GIT_OPTIONS_WITH_VALUE else 1
    return argv[index] if index < len(argv) else ""


def check_read_only(command: str) -> str | None:
    stripped = command.strip()
    if any(token in stripped for token in CHAIN_TOKENS):
        return "只读角色不能使用重定向、管道或命令串联"
    if "--output" in stripped:
        return "只读角色不能使用 --output 写入文件"
    try:
        argv = shlex.split(stripped)
    except ValueError:
        return "无法解析命令"
    if len(argv) < 2 or argv[0] != "git" or argv[1] not in READ_ONLY_GIT:
        return "只读角色只能运行 git status、diff、log、show、rev-parse"
    return None


def check_no_delivery(command: str) -> str | None:
    for segment in SEGMENT_SPLIT.split(command):
        # Subshells and groups only wrap commands; treat their brackets as spaces.
        segment = re.sub(r"[(){}]", " ", segment).strip()
        if not segment:
            continue
        try:
            argv = shlex.split(segment)
        except ValueError:
            argv = segment.split()
        if git_subcommand(argv) in DELIVERY_GIT:
            return "该角色不负责 Git 交付，暂存、提交、分支和历史操作交给父 Agent"
    return None


CHECKS = {"read-only": check_read_only, "no-delivery": check_no_delivery}


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in CHECKS:
        print(f"usage: bash_guard.py {{{'|'.join(CHECKS)}}}", file=sys.stderr)
        return 2
    try:
        payload = json.load(sys.stdin)
        command = payload["tool_input"]["command"]
        if not isinstance(command, str):
            raise TypeError("command is not a string")
    except (ValueError, KeyError, TypeError) as error:
        print(f"bash_guard: 无法读取 Bash 命令 ({error})", file=sys.stderr)
        return 2
    reason = CHECKS[argv[1]](command)
    if reason:
        print(f"bash_guard: 已拒绝：{reason}：{command}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
