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
    "scripts/core.py",
    "scripts/design_system.py",
    "scripts/search.py",
    "data/colors.csv",
    "data/styles.csv",
    "data/ui-reasoning.csv",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Update the shared ui-ux-pro-max skill from its official npm CLI package."
    )
    parser.add_argument(
        "--version",
        default="latest",
        help="npm package version to install; defaults to latest",
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
    generated = work_dir / ".codex" / "skills" / SKILL_NAME
    if not generated.is_dir():
        raise SystemExit(
            "[!] Official CLI did not generate the expected skill directory.\n"
            f"Expected: {generated}\n"
            f"CLI output:\n{result.stdout}"
        )
    return generated


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


def content_digest(skill_dir: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in skill_dir.rglob("*") if item.is_file()):
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
        "content_sha256": digest,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    temporary = LOCK_FILE.with_name(f".{LOCK_FILE.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
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


def install_skill(generated: Path, version: str, digest: str, keep_backups: int) -> Path | None:
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    candidate = SKILLS_DIR / f".{SKILL_NAME}.candidate-{uuid.uuid4().hex}"
    shutil.copytree(generated, candidate)

    current_digest = content_digest(TARGET_DIR) if TARGET_DIR.is_dir() else None
    if current_digest == digest:
        shutil.rmtree(candidate)
        write_lock(version, digest)
        print("[=] Skill content is already current; lock metadata refreshed.")
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
    version = resolve_version(npm, args.version)

    print(f"[*] Source: {UPSTREAM_URL}")
    print(f"[*] Package: {PACKAGE_NAME}@{version}")
    print(f"[*] Target: {TARGET_DIR}")

    with tempfile.TemporaryDirectory(prefix="agent-toolkit-ui-ux-") as temporary:
        work_dir = Path(temporary)
        generated = generate_skill(npx, version, work_dir)
        validate_skill(generated)
        smoke_test(generated)
        digest = content_digest(generated)
        file_count = sum(1 for path in generated.rglob("*") if path.is_file())

        print(f"[+] Generated and validated {file_count} files")
        print(f"[+] Content SHA-256: {digest}")
        if args.dry_run:
            print("[+] Dry run complete; current skill was not changed.")
            return 0

        backup = install_skill(generated, version, digest, args.keep_backups)

    if backup:
        print(f"[+] Previous version backed up to: {backup}")
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
