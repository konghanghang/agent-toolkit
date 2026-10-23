from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

COMMON_DIR = Path(__file__).resolve().parents[1]
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from runtime import load_runtime_or_exit
from work_config import WorkConfigError, load_work_config


SESSION_ERROR_RE = re.compile(r"session 可能已过期|多半 session 已失效|找不到凭证。请先跑:|响应不是 JSON, 多半 session 已失效|找不到 session 文件")


def usage() -> str:
    return """用法:
  archery login
  archery list-instances
  archery list-databases --scope domestic
  archery list-databases --scope overseas
  archery list-databases --module example-module
  archery list-tables --instance <instance> --database <database>
  archery list-instances-safe [--format table|json]
  archery list-databases-safe --scope domestic|overseas
  archery list-tables-safe --instance <instance> --database <database>
  archery query --scope domestic --database <database> --sql '<sql>'
  archery query --module example-module --database <database> --sql '<sql>'

说明:
  - Python / AGENT_TOOLKIT_ROOT / TMP_DIR 来自 工作运行时配置
  - 未初始化时请先运行: python3 $AGENT_TOOLKIT_ROOT/common/bootstrap_work.py
  - 底层脚本来自 $AGENT_TOOLKIT_ROOT/skills/archery-prod-query/scripts
  - 凭证与 session 目录来自 工作运行时配置里的 archery_dir
  - list/query 在 session 缺失或失效时会自动转去登录，再重试一次
  - 三个 *-safe 元数据入口会在需要时自动登录，且仅开放元数据参数，供 Codex Rules 默认放行
  - 轻路由只负责自动补 --instance，不会自动猜数据库名或 SQL
  - --instance 显式指定时优先级最高
  - --scope 与 --module 从私有工作配置的 archery.scopes / archery.modules 读取实例名
  - 缺少映射时停止；也可用 --instance 显式指定已确认实例
"""


def wants_help(argv: list[str]) -> bool:
    return any(arg in {"-h", "--help"} for arg in argv)


def runtime_and_paths() -> tuple[dict, Path, Path]:
    runtime = load_runtime_or_exit(__file__)
    toolkit_root = Path(runtime["agent_toolkit_root"])
    skill_root = toolkit_root / "skills" / "archery-prod-query"
    return runtime, skill_root / "scripts" / "archery_login.py", skill_root / "scripts" / "archery_query.py"


def require_credentials(runtime: dict) -> None:
    cred_file = Path(runtime["archery_dir"]) / "archery-credentials.json"
    if not cred_file.exists():
        raise SystemExit(f"[!] 找不到凭证文件: {cred_file}\n    请先按 skill 文档创建该文件")


def run_subprocess(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True)


def print_output(result: subprocess.CompletedProcess[str]) -> None:
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)


def normalize_scope(scope: str) -> str:
    mapping = {"domestic": "domestic", "cn": "domestic", "国内": "domestic", "overseas": "overseas", "intl": "overseas", "global": "overseas", "海外": "overseas"}
    if scope not in mapping:
        raise SystemExit(f"[!] 不支持的 --scope: {scope}\n    可选值: domestic|cn|国内|overseas|intl|global|海外")
    return mapping[scope]


def normalize_module(module: str) -> str:
    if not module.strip():
        raise SystemExit("[!] --module/--service 不能为空")
    return module.strip()


def configured_instance(scope: str, module: str) -> str:
    try:
        config = load_work_config()
    except WorkConfigError as exc:
        raise SystemExit(f"[!] {exc}") from exc
    section = config.get("archery")
    if not isinstance(section, dict):
        raise SystemExit("[!] 工作配置缺少 archery 实例映射。")
    kind, name = ("scopes", scope) if scope == "overseas" or not module else ("modules", module)
    values = section.get(kind, {})
    if not isinstance(values, dict):
        raise SystemExit(f"[!] archery.{kind} 必须是对象。")
    instance = values.get(name)
    if not isinstance(instance, str) or not instance.strip():
        raise SystemExit(f"[!] archery.{kind} 缺少所选名称的非空实例配置；请填写私有配置或显式指定 --instance。")
    return instance.strip()


def print_route_help(cmd: str) -> None:
    if cmd not in {"list-databases", "list-tables", "query"}:
        return
    print(
        "archery 入口额外支持轻路由参数:\n"
        "  --scope domestic|overseas\n"
        "  --module <私有配置中的模块名>\n"
        "  --service <value>\n"
        "说明:\n"
        "  - --service 是 --module 的别名\n"
        "  - 只自动补 --instance\n"
        "  - --instance 显式指定时优先级最高\n"
    )


def resolve_instance_args(args: list[str]) -> list[str]:
    cmd = args[0]
    scope = module = explicit_instance = ""
    pass_args = [cmd]
    i = 1
    while i < len(args):
        arg = args[i]
        if arg == "--scope":
            scope = normalize_scope(args[i + 1])
            i += 2
        elif arg in {"--module", "--service"}:
            module = normalize_module(args[i + 1])
            i += 2
        elif arg == "--instance":
            explicit_instance = args[i + 1]
            pass_args.extend([arg, args[i + 1]])
            i += 2
        else:
            pass_args.append(arg)
            i += 1
    if cmd == "list-instances" or explicit_instance:
        return pass_args
    if not scope and not module:
        raise SystemExit(
            "[!] 请显式指定 --instance，或使用私有配置中的 --scope/--module。"
        )
    route_instance = configured_instance(scope, module)
    pass_args.extend(["--instance", route_instance])
    print(f"[*] 路由 instance: {route_instance}", file=sys.stderr)
    return pass_args


def resolve_metadata_safe_args(argv: list[str]) -> list[str]:
    cmd = argv[0]
    raw_cmd = cmd.removesuffix("-safe")
    parser = argparse.ArgumentParser(prog=f"archery {cmd}", allow_abbrev=False)
    parser.add_argument("--format", choices=["table", "json"], default="table")
    if raw_cmd in {"list-databases", "list-tables"}:
        route = parser.add_mutually_exclusive_group(required=True)
        route.add_argument("--instance")
        route.add_argument("--scope", choices=["domestic", "overseas"])
        route.add_argument("--module")
    if raw_cmd == "list-tables":
        parser.add_argument("--database", required=True)
    args = parser.parse_args(argv[1:])
    resolved = [raw_cmd, "--format", args.format]
    for name in ("instance", "scope", "module", "database"):
        value = getattr(args, name, None)
        if value is not None:
            resolved.extend(["--" + name, value])
    return resolved


def run_login(args: list[str]) -> int:
    runtime, login_script, _ = runtime_and_paths()
    if wants_help(args):
        result = run_subprocess([runtime["python_bin"], str(login_script), "--help"])
        print_output(result)
        return result.returncode
    require_credentials(runtime)
    result = run_subprocess([runtime["python_bin"], str(login_script), *args])
    print_output(result)
    return result.returncode


def run_login_auto() -> int:
    runtime, login_script, _ = runtime_and_paths()
    require_credentials(runtime)
    result = run_subprocess([runtime["python_bin"], str(login_script), "--no-keep"])
    print_output(result)
    return result.returncode


def run_data_command(args: list[str]) -> int:
    runtime, _, query_script = runtime_and_paths()
    if wants_help(args):
        print_route_help(args[0])
        result = run_subprocess([runtime["python_bin"], str(query_script), *args])
        print_output(result)
        return result.returncode
    resolved_args = resolve_instance_args(args)
    require_credentials(runtime)
    session_file = Path(runtime["archery_dir"]) / "archery_session.json"
    if not session_file.exists():
        print("[*] 未找到 session，先执行登录")
        code = run_login_auto()
        if code != 0:
            return code
    result = run_subprocess([runtime["python_bin"], str(query_script), *resolved_args])
    if SESSION_ERROR_RE.search(result.stdout + result.stderr):
        print_output(result)
        print("[*] 检测到 session 失效，转去重新登录")
        code = run_login_auto()
        if code != 0:
            return code
        result = run_subprocess([runtime["python_bin"], str(query_script), *resolved_args])
    print_output(result)
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in {"help", "-h", "--help"}:
        print(usage(), end="")
        return 0
    cmd, args = argv[0], argv[1:]
    if cmd == "login":
        return run_login(args)
    if cmd in {"list-instances", "list-databases", "list-tables", "query"}:
        return run_data_command([cmd, *args])
    if cmd in {"list-instances-safe", "list-databases-safe", "list-tables-safe"}:
        return run_data_command(resolve_metadata_safe_args([cmd, *args]))
    print(f"[!] 未知子命令: {cmd}")
    print(usage(), end="")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
