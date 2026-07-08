from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict


CONFIG_DIR = Path.home() / ".local" / "agent-toolkit" / "work"
CONFIG_FILE = CONFIG_DIR / "runtime.json"
REQUIRED_KEYS = ("work_root", "skills_root", "python_bin", "tmp_dir", "sls_dir", "archery_dir")


class RuntimeConfigError(RuntimeError):
    pass


def detect_skills_root(current_file: str | Path) -> Path:
    current = Path(current_file).resolve()
    for parent in (current.parent, *current.parents):
        if (parent / "common" / "runtime.py").exists() and (parent / "common" / "bootstrap_work.py").exists():
            return parent
    raise RuntimeConfigError(f"无法从路径定位技能根目录: {current}")


def bootstrap_script_path(current_file: str | Path) -> Path:
    return detect_skills_root(current_file) / "common" / "bootstrap_work.py"


def init_hint(current_file: str | Path) -> str:
    bootstrap = bootstrap_script_path(current_file)
    python_hint = sys.executable or "python3"
    return f"{python_hint} {bootstrap}"


def _validate_path(path_str: str, key: str, current_file: str | Path) -> None:
    path = Path(path_str).expanduser()
    if key in {"tmp_dir", "sls_dir", "archery_dir"}:
        if not path.exists() or not path.is_dir():
            raise RuntimeConfigError(
                f"[!] 运行时配置里的 {key} 不可用: {path}\n"
                f"    请重新初始化: {init_hint(current_file)}"
            )
        return

    if not path.exists():
        raise RuntimeConfigError(
            f"[!] 运行时配置里的 {key} 路径不存在: {path}\n"
            f"    请重新初始化: {init_hint(current_file)}"
        )


def load_runtime(current_file: str | Path) -> Dict[str, Any]:
    current_file = str(Path(current_file).resolve())
    if not CONFIG_FILE.exists():
        raise RuntimeConfigError(
            f"[!] 未找到 work 运行时配置: {CONFIG_FILE}\n"
            f"    请先初始化: {init_hint(current_file)}"
        )

    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeConfigError(
            f"[!] work 运行时配置解析失败 ({exc}): {CONFIG_FILE}\n"
            f"    请重新初始化: {init_hint(current_file)}"
        ) from exc

    if not data.get("skills_root") and data.get("codex_home"):
        legacy_skills_root = Path(str(data["codex_home"])).expanduser() / "skills"
        if legacy_skills_root.exists():
            data["skills_root"] = str(legacy_skills_root.resolve())

    missing = [key for key in REQUIRED_KEYS if not data.get(key)]
    if missing:
        raise RuntimeConfigError(
            f"[!] work 运行时配置缺少字段: {', '.join(missing)}\n"
            f"    请重新初始化: {init_hint(current_file)}"
        )

    actual_skills_root = detect_skills_root(current_file)
    configured_skills_root = Path(str(data["skills_root"])).expanduser().resolve()
    if configured_skills_root != actual_skills_root:
        raise RuntimeConfigError(
            f"[!] 运行时配置里的 skills_root 与当前技能目录不一致:\n"
            f"    config: {configured_skills_root}\n"
            f"    actual: {actual_skills_root}\n"
            f"    请重新初始化: {init_hint(current_file)}"
        )

    for key in ("work_root", "python_bin", "tmp_dir", "sls_dir", "archery_dir"):
        _validate_path(str(data[key]), key, current_file)

    normalized = dict(data)
    normalized["work_root"] = str(Path(str(data["work_root"])).expanduser().resolve())
    normalized["skills_root"] = str(configured_skills_root)
    normalized["python_bin"] = str(Path(str(data["python_bin"])).expanduser().absolute())
    normalized["tmp_dir"] = str(Path(str(data["tmp_dir"])).expanduser().resolve())
    normalized["sls_dir"] = str(Path(str(data["sls_dir"])).expanduser().resolve())
    normalized["archery_dir"] = str(Path(str(data["archery_dir"])).expanduser().resolve())
    return normalized


def load_runtime_or_exit(current_file: str | Path) -> Dict[str, Any]:
    try:
        return load_runtime(current_file)
    except RuntimeConfigError as exc:
        raise SystemExit(str(exc))


def main() -> int:
    parser = argparse.ArgumentParser(description="work runtime config helper")
    parser.add_argument("--get", choices=(*REQUIRED_KEYS, "codex_home"), help="输出某个运行时配置字段")
    args = parser.parse_args()

    runtime = load_runtime_or_exit(__file__)
    if args.get:
        if args.get == "codex_home":
            print(runtime["skills_root"])
        else:
            print(runtime[args.get])
    else:
        print(json.dumps(runtime, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
