#!/usr/bin/env python3
"""Install Agent Toolkit skills and Codex rules with safe symlinks."""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path


TOOLKIT_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = TOOLKIT_ROOT / "skills"
CODEX_RULES_SOURCE = TOOLKIT_ROOT / "codex" / "rules"
CODEX_SKILLS_TARGET = Path.home() / ".agents" / "skills"
CLAUDE_SKILLS_TARGET = Path.home() / ".claude" / "skills"
CODEX_RULES_TARGET = Path.home() / ".codex" / "rules"
LEGACY_ROOTS = (
    Path.home() / ".shared-skills",
    TOOLKIT_ROOT,
)


@dataclass(frozen=True)
class LinkSpec:
    source: Path
    target: Path
    label: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install shared skills for Codex and Claude Code, plus Codex rules."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--codex-only", action="store_true", help="Install only Codex skills and rules")
    mode.add_argument("--claude-only", action="store_true", help="Install only Claude Code skills")
    parser.add_argument("--skill", action="append", default=[], help="Install only the named skill; repeatable")
    parser.add_argument("--dry-run", action="store_true", help="Print planned changes without writing")
    parser.add_argument(
        "--replace-managed",
        action="store_true",
        help="Replace symlinks that point to the legacy toolkit root or an old layout",
    )
    return parser.parse_args()


def available_skills() -> dict[str, Path]:
    if not SKILLS_DIR.is_dir():
        raise SystemExit(f"Skills directory does not exist: {SKILLS_DIR}")
    return {
        path.name: path
        for path in sorted(SKILLS_DIR.iterdir())
        if path.is_dir() and (path / "SKILL.md").is_file()
    }


def selected_skills(names: list[str]) -> dict[str, Path]:
    available = available_skills()
    if not names:
        return available
    unknown = sorted(set(names) - set(available))
    if unknown:
        raise SystemExit(f"Unknown skill: {', '.join(unknown)}")
    return {name: available[name] for name in names}


def resolve_link_target(target: Path) -> Path:
    raw = Path(os.readlink(target))
    if not raw.is_absolute():
        raw = target.parent / raw
    return raw.expanduser().resolve(strict=False)


def is_managed_target(path: Path) -> bool:
    for root in LEGACY_ROOTS:
        try:
            path.relative_to(root.expanduser().resolve(strict=False))
            return True
        except ValueError:
            continue
    return False


def plan_links(args: argparse.Namespace) -> list[LinkSpec]:
    skills = selected_skills(args.skill)
    specs: list[LinkSpec] = []

    if not args.claude_only:
        specs.extend(
            LinkSpec(source, CODEX_SKILLS_TARGET / name, "Codex skill")
            for name, source in skills.items()
        )
        if CODEX_RULES_SOURCE.is_dir():
            specs.extend(
                LinkSpec(rule, CODEX_RULES_TARGET / rule.name, "Codex rule")
                for rule in sorted(CODEX_RULES_SOURCE.glob("*.rules"))
            )

    if not args.codex_only:
        specs.extend(
            LinkSpec(source, CLAUDE_SKILLS_TARGET / name, "Claude Code skill")
            for name, source in skills.items()
        )

    return specs


def install_link(spec: LinkSpec, args: argparse.Namespace) -> tuple[str, bool]:
    source = spec.source.resolve()
    target = spec.target

    if os.path.lexists(target):
        if not target.is_symlink():
            return f"CONFLICT {spec.label}: {target} exists and is not a symlink", False

        existing = resolve_link_target(target)
        if existing == source:
            return f"OK       {spec.label}: {target} -> {source}", True

        if not args.replace_managed or not is_managed_target(existing):
            return f"CONFLICT {spec.label}: {target} -> {existing}", False

        action = "REPLACE"
    else:
        action = "CREATE "

    message = f"{action} {spec.label}: {target} -> {source}"
    if args.dry_run:
        return message, True

    target.parent.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(target):
        target.unlink()
    target.symlink_to(source, target_is_directory=source.is_dir())
    return message, True


def main() -> int:
    args = parse_args()
    specs = plan_links(args)
    failures = 0

    for spec in specs:
        message, success = install_link(spec, args)
        print(message)
        if not success:
            failures += 1

    if failures:
        print(f"\n{failures} conflict(s); no conflicting path was overwritten.", file=sys.stderr)
        return 1

    mode = "dry-run" if args.dry_run else "complete"
    print(f"\nInstallation {mode}: {len(specs)} link(s) checked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
