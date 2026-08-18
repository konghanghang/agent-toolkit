from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from runtime import RuntimeConfigError, detect_agent_toolkit_root, normalize_http_base_url

CONFIG_DIR = Path.home() / ".local" / "agent-toolkit" / "work"
CONFIG_FILE = CONFIG_DIR / "runtime.json"
REQUIREMENTS_FILE_NAME = "requirements.txt"


def platform_default_python(toolkit_root: Path) -> Path:
    if os.name == "nt":
        return toolkit_root / ".venv" / "Scripts" / "python.exe"
    return toolkit_root / ".venv" / "bin" / "python"


def configured_python_path(python_bin: Path) -> Path:
    """Keep the virtualenv entrypoint instead of resolving its interpreter symlink."""
    return python_bin.expanduser().absolute()


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


def prompt_url(label: str, default: str | None = None) -> str:
    while True:
        suffix = f" [{default}]" if default else ""
        raw = input(f"{label}{suffix}: ").strip() or (default or "")
        if not raw:
            print("  [!] 不能为空")
            continue
        try:
            return normalize_http_base_url(raw, label)
        except RuntimeConfigError as exc:
            print(f"  [!] {exc}")


def ensure_venv(python_bin: Path, system_python: str) -> None:
    if python_bin.exists():
        return
    if not prompt_yes_no(f"未找到解释器 {python_bin}，是否自动创建虚拟环境？", default=True):
        raise SystemExit("[!] 未创建虚拟环境，初始化终止。")
    venv_dir = python_bin.parent.parent if os.name != "nt" else python_bin.parent.parent
    subprocess.run([system_python, "-m", "venv", str(venv_dir)], check=True)
    if not python_bin.exists():
        raise SystemExit(f"[!] 虚拟环境创建后仍找不到解释器: {python_bin}")


def install_dependencies(python_bin: Path, requirements_file: Path) -> None:
    if not requirements_file.is_file():
        raise SystemExit(f"[!] 找不到 Python 依赖清单: {requirements_file}")
    if not prompt_yes_no(
        f"是否从 {requirements_file.name} 安装项目依赖？",
        default=True,
    ):
        return
    subprocess.run(
        [str(python_bin), "-m", "pip", "install", "-r", str(requirements_file)],
        check=True,
    )


def install_command_templates(toolkit_root: Path) -> None:
    template_dir = toolkit_root / "common" / "templates" / "bin"
    target_dir = toolkit_root / "bin"
    target_dir.mkdir(parents=True, exist_ok=True)

    if os.name == "nt":
        names = [
            "sls.cmd",
            "archery.cmd",
            "jms.cmd",
            "testdb.cmd",
            "sls.ps1",
            "archery.ps1",
            "jms.ps1",
            "testdb.ps1",
        ]
    else:
        names = ["sls", "archery", "jms", "testdb"]

    for name in names:
        src = template_dir / name
        dst = target_dir / name
        if not src.exists():
            raise SystemExit(f"[!] 命令模板不存在: {src}")
        shutil.copy2(src, dst)
        if os.name != "nt":
            dst.chmod(0o755)


def initialize_test_db_profile(
    toolkit_root: Path,
    python_bin: Path,
    test_db_dir: Path,
) -> None:
    initializer = toolkit_root / "skills" / "verify-test-db" / "scripts" / "init_config.py"
    if not initializer.is_file():
        raise SystemExit(f"[!] 找不到测试库配置初始化器: {initializer}")
    subprocess.run(
        [str(python_bin), str(initializer), "--base-dir", str(test_db_dir)],
        check=True,
    )


def main() -> int:
    toolkit_root = detect_agent_toolkit_root(__file__)
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
    print(f"  AGENT_TOOLKIT_ROOT: {toolkit_root}")
    print(f"  配置文件: {CONFIG_FILE}")

    work_root = prompt_path("work 工作区根目录 WORK_ROOT", str(guessed_work_root))
    tmp_dir_default = existing.get("tmp_dir") or tempfile.gettempdir()
    tmp_dir = prompt_path("临时目录 TMP_DIR", tmp_dir_default)
    prometheus_base_url = prompt_url(
        "Prometheus 地址 PROMETHEUS_BASE_URL",
        existing.get("prometheus_base_url"),
    )
    sls_dir_default = existing.get("sls_dir") or str(CONFIG_DIR / "sls")
    sls_dir = prompt_path("SLS 凭证与 session 目录 SLS_DIR", sls_dir_default)
    archery_dir_default = existing.get("archery_dir") or str(CONFIG_DIR / "archery")
    archery_dir = prompt_path("Archery 凭证与 session 目录 ARCHERY_DIR", archery_dir_default)
    jms_dir_default = existing.get("jms_dir") or str(CONFIG_DIR / "jms")
    jms_dir = prompt_path("JumpServer 凭证与 session 目录 JMS_DIR", jms_dir_default)
    test_db_dir_default = existing.get("test_db_dir") or str(CONFIG_DIR / "test-db")
    test_db_dir = prompt_path("测试库配置与快照目录 TEST_DB_DIR", test_db_dir_default)
    python_default = existing.get("python_bin") or str(platform_default_python(toolkit_root))
    python_bin = prompt_path("统一 Python 解释器 PYTHON_BIN", python_default)

    if not work_root.exists():
        raise SystemExit(f"[!] WORK_ROOT 不存在: {work_root}")
    if not tmp_dir.exists():
        raise SystemExit(f"[!] TMP_DIR 不存在: {tmp_dir}")
    sls_dir.mkdir(parents=True, exist_ok=True)
    archery_dir.mkdir(parents=True, exist_ok=True)
    jms_dir.mkdir(parents=True, exist_ok=True)
    test_db_dir.mkdir(parents=True, exist_ok=True)

    ensure_venv(python_bin, sys.executable or "python3")
    install_dependencies(python_bin, toolkit_root / REQUIREMENTS_FILE_NAME)
    install_command_templates(toolkit_root)

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config = {
        "work_root": str(work_root.resolve()),
        "agent_toolkit_root": str(toolkit_root.resolve()),
        "skills_root": str(toolkit_root.resolve()),
        "python_bin": str(configured_python_path(python_bin)),
        "tmp_dir": str(tmp_dir.resolve()),
        "prometheus_base_url": prometheus_base_url,
        "sls_dir": str(sls_dir.resolve()),
        "archery_dir": str(archery_dir.resolve()),
        "jms_dir": str(jms_dir.resolve()),
        "test_db_dir": str(test_db_dir.resolve()),
    }
    CONFIG_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    initialize_test_db_profile(toolkit_root, python_bin, test_db_dir)

    print("\n[+] work 运行时配置已写入:")
    print(f"    {CONFIG_FILE}")
    print("[+] Prometheus 地址已写入本机运行时配置")
    print("[+] 建议确认 PATH 已包含:")
    print(f"    {toolkit_root / 'bin'}")
    print("[+] 需要补充的凭证文件:")
    print(f"    {sls_dir / 'aliyun-credentials.json'}")
    print(f"    {archery_dir / 'archery-credentials.json'}")
    print(f"    {jms_dir / 'jms-credentials.json'}")
    print("[+] 测试库 profile 模板（请替换地址和账号，并设置密码环境变量）:")
    print(f"    {test_db_dir / 'profiles.json'}")
    print("[+] session 文件会自动写到:")
    print(f"    {sls_dir / 'aliyun_session.json'}")
    print(f"    {archery_dir / 'archery_session.json'}")
    print(f"    {jms_dir / 'jms_session.json'}")
    print("[+] 后续可直接使用:")
    print("    sls login")
    print("    archery list-instances")
    print("    jms logs --service data-center")
    print("    testdb profiles-safe")
    print("[+] 跨平台基础入口始终可用:")
    print("    PYTHON_BIN + AGENT_TOOLKIT_ROOT/skills/.../scripts/*.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
