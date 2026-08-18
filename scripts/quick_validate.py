#!/usr/bin/env python3
"""Validate the portable metadata contract of one Skill directory."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml


MAX_SKILL_NAME_LENGTH = 64
ALLOWED_PROPERTIES = {"name", "description", "license", "allowed-tools", "metadata"}
FRONTMATTER_RE = re.compile(r"^---\r?\n(.*?)\r?\n---(?:\r?\n|$)", re.DOTALL)


def validate_skill(skill_path: str | Path) -> tuple[bool, str]:
    """Return whether a Skill has valid, portable frontmatter."""
    skill_md = Path(skill_path) / "SKILL.md"
    if not skill_md.is_file():
        return False, "SKILL.md not found"

    try:
        content = skill_md.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return False, f"Unable to read SKILL.md as UTF-8: {exc}"

    match = FRONTMATTER_RE.match(content)
    if not match:
        return False, "Invalid or missing YAML frontmatter"

    try:
        frontmatter = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        return False, f"Invalid YAML in frontmatter: {exc}"
    if not isinstance(frontmatter, dict):
        return False, "Frontmatter must be a YAML dictionary"

    unexpected_keys = set(frontmatter) - ALLOWED_PROPERTIES
    if unexpected_keys:
        allowed = ", ".join(sorted(ALLOWED_PROPERTIES))
        unexpected = ", ".join(sorted(unexpected_keys))
        return (
            False,
            f"Unexpected key(s) in SKILL.md frontmatter: {unexpected}. "
            f"Allowed properties are: {allowed}",
        )

    if "name" not in frontmatter:
        return False, "Missing 'name' in frontmatter"
    if "description" not in frontmatter:
        return False, "Missing 'description' in frontmatter"

    name = frontmatter["name"]
    if not isinstance(name, str):
        return False, f"Name must be a string, got {type(name).__name__}"
    name = name.strip()
    if not name:
        return False, "Name cannot be empty"
    if not re.fullmatch(r"[a-z0-9-]+", name):
        return (
            False,
            f"Name '{name}' should be hyphen-case "
            "(lowercase letters, digits, and hyphens only)",
        )
    if name.startswith("-") or name.endswith("-") or "--" in name:
        return (
            False,
            f"Name '{name}' cannot start/end with hyphen or contain consecutive hyphens",
        )
    if len(name) > MAX_SKILL_NAME_LENGTH:
        return (
            False,
            f"Name is too long ({len(name)} characters). "
            f"Maximum is {MAX_SKILL_NAME_LENGTH} characters.",
        )

    description = frontmatter["description"]
    if not isinstance(description, str):
        return (
            False,
            f"Description must be a string, got {type(description).__name__}",
        )
    description = description.strip()
    if not description:
        return False, "Description cannot be empty"
    if "<" in description or ">" in description:
        return False, "Description cannot contain angle brackets (< or >)"
    if len(description) > 1024:
        return (
            False,
            f"Description is too long ({len(description)} characters). "
            "Maximum is 1024 characters.",
        )

    return True, "Skill is valid!"


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("Usage: python quick_validate.py <skill_directory>")
        return 2

    valid, message = validate_skill(args[0])
    print(message)
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
