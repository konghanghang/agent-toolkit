from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from runtime import detect_skills_root

CONFIG_DIR = Path.home() / ".local" / "agent-toolkit" / "work"
CONFIG_FILE = CONFIG_DIR / "runtime.json"
DEPENDENCIES = ["selenium", "webdriver-manager", "pyotp", "requests"]


def platform_default_python(work_root: Path) -> Path:
    if os.name == "nt":
        return work_root / ".venv" / "Scripts" / "python.exe"
    return work_root / ".venv" / "bin" / "python"


def prompt_path(label: str, default: str | None = None, required: bool = True) -> Path:
    while True:
        suffix = f" [{default}]" if default else ""
        raw = input(f"{label}{suffix}: ").strip()
        if not raw:
            raw = default or ""
        if not raw:
            if required:
                print("  [!] 不能为空")
                continue
            return Path()
        return Path(raw).expanduser()


def prompt_yes_no(label: str, default: bool = True) -> bool:
    default_text = "Y/n" if default else "y/N"
    raw = input(f"{label} [{default_text}]: ").strip().lower()
    if not raw:
        return default
    return raw in {"y", "yes"}


def ensure_venv(python_bin: Path, system_python: str) -> None:
    if python_bin.exists():
        return
    if not prompt_yes_no(f"未找到解释器 {python_bin}，是否自动创建虚拟环境？", default=True):
        raise SystemExit("[!] 未创建虚拟环境，初始化终止。")
    venv_dir = python_bin.parent.parent if os.name != "nt" else python_bin.parent.parent
    subprocess.run([system_python, "-m", "venv", str(venv_dir)], check=True)
    if not python_bin.exists():
        raise SystemExit(f"[!] 虚拟环境创建后仍找不到解释器: {python_bin}")


def install_dependencies(python_bin: Path) -> None:
    if not prompt_yes_no("是否安装通用依赖 selenium/webdriver-manager/pyotp/requests？", default=True):
        return
    subprocess.run([str(python_bin), "-m", "pip", "install", *DEPENDENCIES], check=True)


def install_command_templates(skills_root: Path) -> None:
    template_dir = skills_root / "common" / "templates" / "bin"
    target_dir = skills_root / "bin"
    target_dir.mkdir(parents=True, exist_ok=True)

    if os.name == "nt":
        names = ["sls.cmd", "archery.cmd", "sls.ps1", "archery.ps1"]
    else:
        names = ["sls", "archery"]

    for name in names:
        src = template_dir / name
        dst = target_dir / name
        if not src.exists():
            raise SystemExit(f"[!] 命令模板不存在: {src}")
        shutil.copy2(src, dst)
        if os.name != "nt":
            dst.chmod(0o755)


def main() -> int:
    skills_root = detect_skills_root(__file__)
    existing = {}
    if CONFIG_FILE.exists():
        try:
            existing = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            existing = {}

    guessed_work_root = Path(existing.get("work_root", "")).expanduser()
    if not guessed_work_root.exists():
        if (Path.home() / "data" / "work").exists():
            guessed_work_root = Path.home() / "data" / "work"
        elif os.name == "nt" and Path("D:/Code").exists():
            guessed_work_root = Path("D:/Code")
        else:
            guessed_work_root = Path.cwd()

    print("work 运行时初始化")
    print(f"  SKILLS_ROOT: {skills_root}")
    print(f"  配置文件: {CONFIG_FILE}")

    work_root = prompt_path("work 工作区根目录 WORK_ROOT", str(guessed_work_root))
    tmp_dir_default = existing.get("tmp_dir") or tempfile.gettempdir()
    tmp_dir = prompt_path("临时目录 TMP_DIR", tmp_dir_default)
    sls_dir_default = existing.get("sls_dir") or str(CONFIG_DIR / "sls")
    sls_dir = prompt_path("SLS 凭证与 session 目录 SLS_DIR", sls_dir_default)
    archery_dir_default = existing.get("archery_dir") or str(CONFIG_DIR / "archery")
    archery_dir = prompt_path("Archery 凭证与 session 目录 ARCHERY_DIR", archery_dir_default)
    python_default = existing.get("python_bin") or str(platform_default_python(work_root))
    python_bin = prompt_path("统一 Python 解释器 PYTHON_BIN", python_default)

    if not work_root.exists():
        raise SystemExit(f"[!] WORK_ROOT 不存在: {work_root}")
    if not tmp_dir.exists():
        raise SystemExit(f"[!] TMP_DIR 不存在: {tmp_dir}")
    sls_dir.mkdir(parents=True, exist_ok=True)
    archery_dir.mkdir(parents=True, exist_ok=True)

    ensure_venv(python_bin, sys.executable or "python3")
    install_dependencies(python_bin)
    install_command_templates(skills_root)

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config = {
        "work_root": str(work_root.resolve()),
        "skills_root": str(skills_root.resolve()),
        "python_bin": str(python_bin.resolve()),
        "tmp_dir": str(tmp_dir.resolve()),
        "sls_dir": str(sls_dir.resolve()),
        "archery_dir": str(archery_dir.resolve()),
    }
    CONFIG_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n[+] work 运行时配置已写入:")
    print(f"    {CONFIG_FILE}")
    print("[+] 建议确认 PATH 已包含:")
    print(f"    {skills_root / 'bin'}")
    print("[+] 需要补充的凭证文件:")
    print(f"    {sls_dir / 'aliyun-credentials.json'}")
    print(f"    {archery_dir / 'archery-credentials.json'}")
    print("[+] session 文件会自动写到:")
    print(f"    {sls_dir / 'aliyun_session.json'}")
    print(f"    {archery_dir / 'archery_session.json'}")
    print("[+] 后续可直接使用:")
    print("    sls login")
    print("    archery list-instances")
    print("[+] 跨平台基础入口始终可用:")
    print("    PYTHON_BIN + SKILLS_ROOT/.../scripts/*.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
