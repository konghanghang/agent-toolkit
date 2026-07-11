from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

COMMON_DIR = Path(__file__).resolve().parents[1]
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from runtime import load_runtime_or_exit


INSTANCE_MAIN_DB = "main-db"
INSTANCE_NEW_BIZ = "polarDB-TiDB"
INSTANCE_OVERSEAS = "海外生产数据库"
SESSION_ERROR_RE = re.compile(r"session 可能已过期|多半 session 已失效|找不到凭证。请先跑:|响应不是 JSON, 多半 session 已失效|找不到 session 文件")


def usage() -> str:
    return """用法:
  archery login
  archery list-instances
  archery list-databases --scope domestic
  archery list-databases --scope overseas
  archery list-databases --module agentcc
  archery list-tables --instance <instance> --database <database>
  archery query --scope domestic --database <database> --sql '<sql>'
  archery query --module sms --database <database> --sql '<sql>'

说明:
  - Python / AGENT_TOOLKIT_ROOT / TMP_DIR 来自 94AI 运行时配置
  - 未初始化时请先运行: python3 $AGENT_TOOLKIT_ROOT/common/bootstrap_94ai.py
  - 底层脚本来自 $AGENT_TOOLKIT_ROOT/skills/archery-prod-query/scripts
  - 凭证与 session 目录来自 94AI 运行时配置里的 archery_dir
  - list/query 在 session 缺失或失效时会自动转去登录，再重试一次
  - 轻路由只负责自动补 --instance，不会自动猜数据库名或 SQL
  - --instance 显式指定时优先级最高
  - --scope overseas -> 海外生产数据库
  - --scope domestic -> main-db
  - --module sms|short-link|wa|callback|agentcc|newbiz -> polarDB-TiDB
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
    mapping = {
        "main": "main", "core": "main", "default": "main", "domestic-main": "main", "主库": "main", "国内主库": "main",
        "newbiz": "newbiz", "new-business": "newbiz", "new": "newbiz", "sms": "newbiz", "短信": "newbiz",
        "short-link": "newbiz", "shortlink": "newbiz", "短链": "newbiz", "wa": "newbiz", "callback": "newbiz",
        "回调": "newbiz", "agentcc": "newbiz", "AgentCC": "newbiz",
        "overseas": "overseas", "intl": "overseas", "global": "overseas", "海外": "overseas",
    }
    if module not in mapping:
        raise SystemExit(f"[!] 不支持的 --module/--service: {module}\n    常用值: main|newbiz|sms|short-link|wa|callback|agentcc|overseas")
    return mapping[module]


def print_route_help(cmd: str) -> None:
    if cmd not in {"list-databases", "list-tables", "query"}:
        return
    print(
        "archery 入口额外支持轻路由参数:\n"
        "  --scope domestic|overseas\n"
        "  --module main|newbiz|sms|short-link|wa|callback|agentcc|overseas\n"
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
    route_instance = ""
    if scope == "overseas" or module == "overseas":
        route_instance = INSTANCE_OVERSEAS
    elif module == "newbiz":
        route_instance = INSTANCE_NEW_BIZ
    elif scope == "domestic" or module == "main":
        route_instance = INSTANCE_MAIN_DB
    if not route_instance:
        raise SystemExit(
            "[!] 未指定 --instance，且无法根据轻路由确定实例。\n"
            "    可选方式：\n"
            "    1. 直接传 --instance <instance>\n"
            "    2. 传 --scope domestic|overseas\n"
            "    3. 传 --module sms|short-link|wa|callback|agentcc|newbiz|main"
        )
    pass_args.extend(["--instance", route_instance])
    print(f"[*] 路由 instance: {route_instance}", file=sys.stderr)
    return pass_args


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
    require_credentials(runtime)
    session_file = Path(runtime["archery_dir"]) / "archery_session.json"
    if not session_file.exists():
        print("[*] 未找到 session，先执行登录")
        code = run_login_auto()
        if code != 0:
            return code
    resolved_args = resolve_instance_args(args)
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
    print(f"[!] 未知子命令: {cmd}")
    print(usage(), end="")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
