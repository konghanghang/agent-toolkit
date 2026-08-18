#!/usr/bin/env python3
"""Update the locally managed ui-ux-pro-max skill from the official CLI package."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path


TOOLKIT_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = TOOLKIT_ROOT / "skills"
SKILL_NAME = "ui-ux-pro-max"
TARGET_DIR = SKILLS_DIR / SKILL_NAME
LOCK_FILE = TOOLKIT_ROOT / "external-skills.lock.json"
BACKUP_DIR = TOOLKIT_ROOT / ".local" / "backups"
PACKAGE_NAME = "ui-ux-pro-max-cli"
UPSTREAM_URL = "https://github.com/nextlevelbuilder/ui-ux-pro-max-skill"
REQUIRED_FILES = (
    "SKILL.md",
    "references/quick-reference.md",
    "scripts/core.py",
    "scripts/design_system.py",
    "scripts/search.py",
    "data/colors.csv",
    "data/styles.csv",
    "data/ui-reasoning.csv",
)
GENERATED_SKILL_PATHS = (
    Path(".agents") / "skills" / SKILL_NAME,
    Path(".codex") / "skills" / SKILL_NAME,
)
LOCAL_TRANSFORM = "progressive-disclosure-v1"
BUNDLED_TEST_SOURCE_REQUIREMENTS = {
    "test_catalog_refresh.py": (
        "scripts/refresh-google-fonts.py",
        "scripts/refresh-icon-catalog.py",
    ),
    "test_relevance_evaluator.py": ("scripts/evaluate-relevance.py",),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Update the shared ui-ux-pro-max skill from its official npm CLI package."
    )
    version_source = parser.add_mutually_exclusive_group()
    version_source.add_argument(
        "--version",
        help="npm package version to install; defaults to latest",
    )
    version_source.add_argument(
        "--from-lock",
        action="store_true",
        help=(
            "materialize the version and content digest from external-skills.lock.json "
            "without rewriting the lock file"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="generate and validate the update without replacing the current skill",
    )
    parser.add_argument(
        "--keep-backups",
        type=int,
        default=3,
        help="number of successful local backups to retain; defaults to 3",
    )
    return parser.parse_args()


def require_command(name: str) -> str:
    command = shutil.which(name)
    if not command:
        raise SystemExit(f"[!] Required command not found: {name}")
    return command


def run(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True,
    )


def resolve_version(npm: str, requested: str) -> str:
    selector = "latest" if requested == "latest" else requested
    npm_cache = Path(tempfile.gettempdir()) / "agent-toolkit-npm-cache"
    npm_cache.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["npm_config_cache"] = str(npm_cache)
    result = run(
        [npm, "view", f"{PACKAGE_NAME}@{selector}", "version", "--json"],
        env=env,
    )
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"[!] Could not parse npm version output:\n{result.stdout}") from exc

    if isinstance(value, list):
        value = value[-1] if value else ""
    version = str(value).strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", version):
        raise SystemExit(f"[!] Unexpected npm version: {version!r}")
    return version


def generate_skill(npx: str, version: str, work_dir: Path) -> Path:
    npm_cache = Path(tempfile.gettempdir()) / "agent-toolkit-npm-cache"
    npm_cache.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["npm_config_cache"] = str(npm_cache)
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    result = run(
        [
            npx,
            "--yes",
            f"{PACKAGE_NAME}@{version}",
            "init",
            "--ai",
            "codex",
        ],
        cwd=work_dir,
        env=env,
    )
    candidates = [work_dir / relative for relative in GENERATED_SKILL_PATHS]
    for generated in candidates:
        if generated.is_dir():
            return generated
    expected = "\n".join(f"- {candidate}" for candidate in candidates)
    raise SystemExit(
        "[!] Official CLI did not generate a supported skill directory.\n"
        f"Expected one of:\n{expected}\n"
        f"CLI output:\n{result.stdout}"
    )


def markdown_anchor(heading: str) -> str:
    normalized = re.sub(r"[^\w\s-]", "", heading.lower())
    return re.sub(r"[-\s]+", "-", normalized).strip("-")


def write_text_lf(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as output:
        output.write(text)


def apply_local_transforms(skill_dir: Path) -> list[str]:
    skill_file = skill_dir / "SKILL.md"
    text = skill_file.read_text(encoding="utf-8")
    quick_marker = "## Quick Reference\n"
    workflow_marker = "## How to Use\n"
    quick_start = text.find(quick_marker)
    workflow_start = text.find(workflow_marker, quick_start + len(quick_marker))
    if quick_start < 0 or workflow_start < 0:
        raise SystemExit(
            "[!] Generated SKILL.md no longer has the expected Quick Reference boundary; "
            "review the upstream document before updating the local transform"
        )

    quick_body = text[quick_start + len(quick_marker) : workflow_start].strip()
    headings = re.findall(r"(?m)^###\s+(.+?)\s*$", quick_body)
    if not quick_body or not headings:
        raise SystemExit("[!] Generated SKILL.md Quick Reference is empty or unstructured")

    references_dir = skill_dir / "references"
    references_dir.mkdir(parents=True, exist_ok=True)
    reference_file = references_dir / "quick-reference.md"
    contents = "\n".join(
        f"- [{heading}](#{markdown_anchor(heading)})" for heading in headings
    )
    write_text_lf(
        reference_file,
        "# UI/UX Quick Reference\n\n"
        "Detailed implementation and review checks extracted from the upstream Skill "
        "for progressive disclosure.\n\n"
        f"## Contents\n\n{contents}\n\n{quick_body}\n",
    )

    replacement = (
        "## Quick Reference\n\n"
        "Read [references/quick-reference.md](references/quick-reference.md) when a task "
        "needs detailed implementation or review checks for accessibility, interaction, "
        "performance, layout, typography, motion, forms, navigation, or charts.\n\n"
    )
    transformed = text[:quick_start] + replacement + text[workflow_start:]
    transformed = transformed.replace(
        "rely on the Quick Reference sections above",
        "read the linked Quick Reference",
    ).replace(
        "Quick Reference checklist above",
        "linked Quick Reference checklist",
    )
    write_text_lf(skill_file, transformed)
    return [LOCAL_TRANSFORM]


def validate_skill(skill_dir: Path) -> None:
    missing = [relative for relative in REQUIRED_FILES if not (skill_dir / relative).is_file()]
    if missing:
        raise SystemExit(f"[!] Generated skill is missing required files: {', '.join(missing)}")

    skill_text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    if not re.search(r"(?m)^name:\s*ui-ux-pro-max\s*$", skill_text):
        raise SystemExit("[!] Generated SKILL.md has an unexpected skill name")

    csv_files = list((skill_dir / "data").rglob("*.csv"))
    if len(csv_files) < 20:
        raise SystemExit(f"[!] Generated data set looks incomplete: only {len(csv_files)} CSV files")


def smoke_test(skill_dir: Path) -> None:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = run(
        [
            sys.executable,
            str(skill_dir / "scripts" / "search.py"),
            "SaaS dashboard",
            "--design-system",
            "-p",
            "AgentToolkitUpdate",
        ],
        env=env,
    )
    if "RECOMMENDED DESIGN SYSTEM" not in result.stdout:
        raise SystemExit("[!] Smoke test completed without the expected design-system output")


def run_bundled_checks(skill_dir: Path) -> list[str]:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    completed: list[str] = []

    tests_dir = skill_dir / "scripts" / "tests"
    if tests_dir.is_dir():
        test_files = sorted(tests_dir.glob("test*.py"))
        if not test_files:
            raise SystemExit("[!] Generated bundled test directory is empty")
        skipped: list[str] = []
        executed = 0
        for test_file in test_files:
            requirements = BUNDLED_TEST_SOURCE_REQUIREMENTS.get(test_file.name, ())
            missing = [
                relative
                for relative in requirements
                if not (skill_dir / relative).is_file()
            ]
            if missing:
                skipped.append(test_file.name)
                continue
            run(
                [
                    sys.executable,
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    str(tests_dir),
                    "-p",
                    test_file.name,
                    "-v",
                ],
                env=env,
            )
            executed += 1
        completed.append(f"bundled unit tests ({executed} files)")
        if skipped:
            completed.append(
                f"source-only tests skipped ({', '.join(skipped)})"
            )

    validator = skill_dir / "scripts" / "validate_data.py"
    if validator.is_file():
        run([sys.executable, str(validator)], env=env)
        completed.append("data validation")

    return completed


def content_digest(skill_dir: Path) -> str:
    digest = hashlib.sha256()
    files = (
        item
        for item in skill_dir.rglob("*")
        if item.is_file()
        and "__pycache__" not in item.relative_to(skill_dir).parts
        and item.suffix.lower() not in {".pyc", ".pyo"}
    )
    for path in sorted(files):
        relative = path.relative_to(skill_dir).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def read_lock() -> dict[str, object]:
    if not LOCK_FILE.exists():
        return {"schema_version": 1, "skills": {}}
    try:
        data = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"[!] Could not read {LOCK_FILE}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"[!] Lock file root must be an object: {LOCK_FILE}")
    data.setdefault("schema_version", 1)
    data.setdefault("skills", {})
    return data


def read_locked_release() -> tuple[str, str]:
    data = read_lock()
    skills = data.get("skills")
    if not isinstance(skills, dict):
        raise SystemExit(f"[!] Lock file skills field must be an object: {LOCK_FILE}")
    entry = skills.get(SKILL_NAME)
    if not isinstance(entry, dict):
        raise SystemExit(f"[!] Lock file has no entry for {SKILL_NAME}: {LOCK_FILE}")

    expected_metadata = {
        "source": UPSTREAM_URL,
        "package": PACKAGE_NAME,
        "install_scope": "main-skill-only",
        "local_transform": LOCAL_TRANSFORM,
    }
    for key, expected in expected_metadata.items():
        if entry.get(key) != expected:
            raise SystemExit(
                f"[!] Locked {SKILL_NAME} {key} must be {expected!r}, "
                f"got {entry.get(key)!r}"
            )

    version = entry.get("version")
    if not isinstance(version, str) or not re.fullmatch(
        r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", version
    ):
        raise SystemExit(f"[!] Locked {SKILL_NAME} version is invalid: {version!r}")
    digest = entry.get("content_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise SystemExit(
            f"[!] Locked {SKILL_NAME} content_sha256 is invalid: {digest!r}"
        )
    return version, digest


def write_lock(version: str, digest: str) -> None:
    data = read_lock()
    skills = data.get("skills")
    if not isinstance(skills, dict):
        raise SystemExit(f"[!] Lock file skills field must be an object: {LOCK_FILE}")
    skills[SKILL_NAME] = {
        "source": UPSTREAM_URL,
        "package": PACKAGE_NAME,
        "version": version,
        "install_scope": "main-skill-only",
        "local_transform": LOCAL_TRANSFORM,
        "content_sha256": digest,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    temporary = LOCK_FILE.with_name(f".{LOCK_FILE.name}.{uuid.uuid4().hex}.tmp")
    write_text_lf(
        temporary,
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
    )
    temporary.replace(LOCK_FILE)


def prune_backups(keep: int) -> None:
    if keep < 0:
        raise SystemExit("[!] --keep-backups must be zero or greater")
    backups = sorted(
        BACKUP_DIR.glob(f"{SKILL_NAME}-*"),
        key=lambda path: path.name,
        reverse=True,
    )
    for backup in backups[keep:]:
        shutil.rmtree(backup)


def install_skill(
    generated: Path,
    version: str,
    digest: str,
    keep_backups: int,
    *,
    update_lock: bool = True,
) -> Path | None:
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    candidate = SKILLS_DIR / f".{SKILL_NAME}.candidate-{uuid.uuid4().hex}"
    shutil.copytree(generated, candidate)

    current_digest = content_digest(TARGET_DIR) if TARGET_DIR.is_dir() else None
    if current_digest == digest:
        shutil.rmtree(candidate)
        if update_lock:
            write_lock(version, digest)
            print("[=] Skill content is already current; lock metadata refreshed.")
        else:
            print("[=] Skill content already matches the locked digest.")
        return None

    backup: Path | None = None
    try:
        if TARGET_DIR.exists():
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup = BACKUP_DIR / f"{SKILL_NAME}-{timestamp}"
            if backup.exists():
                backup = BACKUP_DIR / f"{SKILL_NAME}-{timestamp}-{uuid.uuid4().hex[:8]}"
            TARGET_DIR.rename(backup)

        candidate.rename(TARGET_DIR)
        if update_lock:
            write_lock(version, digest)
    except Exception:
        if TARGET_DIR.exists():
            shutil.rmtree(TARGET_DIR)
        if backup and backup.exists():
            backup.rename(TARGET_DIR)
        if candidate.exists():
            shutil.rmtree(candidate)
        raise

    prune_backups(keep_backups)
    return backup


def main() -> int:
    args = parse_args()
    npm = require_command("npm")
    npx = require_command("npx")
    expected_digest: str | None = None
    if args.from_lock:
        requested_version, expected_digest = read_locked_release()
    else:
        requested_version = args.version or "latest"
    version = resolve_version(npm, requested_version)

    print(f"[*] Source: {UPSTREAM_URL}")
    print(f"[*] Package: {PACKAGE_NAME}@{version}")
    print(f"[*] Target: {TARGET_DIR}")

    with tempfile.TemporaryDirectory(prefix="agent-toolkit-ui-ux-") as temporary:
        work_dir = Path(temporary)
        generated = generate_skill(npx, version, work_dir)
        local_transforms = apply_local_transforms(generated)
        validate_skill(generated)
        bundled_checks = run_bundled_checks(generated)
        smoke_test(generated)
        digest = content_digest(generated)
        if expected_digest is not None and digest != expected_digest:
            raise SystemExit(
                "[!] Generated content does not match the locked SHA-256.\n"
                f"Expected: {expected_digest}\n"
                f"Actual:   {digest}"
            )
        file_count = sum(1 for path in generated.rglob("*") if path.is_file())

        print(f"[+] Generated and validated {file_count} files")
        print(f"[+] Local transforms applied: {', '.join(local_transforms)}")
        if bundled_checks:
            print(f"[+] Bundled checks: {', '.join(bundled_checks)}")
        print(f"[+] Content SHA-256: {digest}")
        if args.dry_run:
            print("[+] Dry run complete; current skill was not changed.")
            return 0

        backup = install_skill(
            generated,
            version,
            digest,
            args.keep_backups,
            update_lock=not args.from_lock,
        )

    if backup:
        print(f"[+] Previous version backed up to: {backup}")
    if args.from_lock:
        print(f"[+] Materialized locked {SKILL_NAME} at {PACKAGE_NAME}@{version}")
        print(f"[+] Lock metadata verified from: {LOCK_FILE}")
    else:
        print(f"[+] Updated {SKILL_NAME} to {PACKAGE_NAME}@{version}")
        print(f"[+] Lock metadata written to: {LOCK_FILE}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        output = exc.stdout or ""
        raise SystemExit(
            f"[!] Command failed ({exc.returncode}): {' '.join(exc.cmd)}\n{output}"
        ) from exc
