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


PROJECT_OVERSEAS = "example-overseas-project"
LOGSTORE_OVERSEAS = "datalogs"
PROJECT_CN_OPENAPI = "example-api-project"
LOGSTORE_CN_OPENAPI = "example-api-project"
PROJECT_CN_DEFAULT = "example-domestic-project"
LOGSTORE_CN_DEFAULT = "datalogs"
SESSION_ERROR_RE = re.compile(r"ConsoleNeedLogin|Invalid CSRF token|找不到 session 文件")


def usage() -> str:
    return """用法:
  sls login [--keep]
  sls query --service ai-open-api [--size 100]
  sls query --scope domestic --service data-center --keyword ERROR
  sls query --scope overseas --service agentcc --minutes 60
  sls query --project <project> [--logstore <logstore>] --query '*'
  sls agg --service <svc> --sql 'SELECT count(*) as c' [--minutes 60|--from F --to T] [--size 100] [--out FILE]

说明:
  - Python / AGENT_TOOLKIT_ROOT / TMP_DIR 来自 work 运行时配置
  - 未初始化时请先运行: python3 $AGENT_TOOLKIT_ROOT/common/bootstrap_work.py
  - 底层脚本来自 $AGENT_TOOLKIT_ROOT/skills/aliyun-sls-log-query/scripts
  - 凭证与 session 目录来自 work 运行时配置里的 sls_dir
  - login 默认在抓取 session 后关闭浏览器，仅 --keep 保留窗口
  - query 会在 session 缺失或失效时自动转去登录，再重试一次
  - agg 走 pSql 聚合(count / GROUP BY / 精确去重总数), 使用 --index + --sql, 避坑见 references/advanced-query.md
  - 海外全部服务: example-overseas-project / datalogs
  - 国内 ai-open-api: example-api-project / example-api-project
  - 国内其他服务: example-domestic-project / datalogs
"""


def wants_help(argv: list[str]) -> bool:
    return any(arg in {"-h", "--help"} for arg in argv)


def runtime_and_paths() -> tuple[dict, Path, Path]:
    runtime = load_runtime_or_exit(__file__)
    toolkit_root = Path(runtime["agent_toolkit_root"])
    skill_root = toolkit_root / "skills" / "aliyun-sls-log-query"
    return runtime, skill_root / "scripts" / "aliyun_login.py", skill_root / "scripts" / "aliyun_sls_query.py"


def require_credentials(runtime: dict) -> None:
    cred_file = Path(runtime["sls_dir"]) / "aliyun-credentials.json"
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
    mapping = {"domestic": "domestic", "cn": "domestic", "overseas": "overseas", "intl": "overseas", "global": "overseas"}
    if scope not in mapping:
        raise SystemExit(f"[!] 不支持的 --scope: {scope}\n    可选值: domestic|cn|overseas|intl|global")
    return mapping[scope]


def infer_logstore_by_project(project: str) -> str | None:
    if project in {PROJECT_OVERSEAS, PROJECT_CN_DEFAULT}:
        return LOGSTORE_OVERSEAS
    if project == PROJECT_CN_OPENAPI:
        return LOGSTORE_CN_OPENAPI
    return None


def infer_service_from_text(text: str) -> str | None:
    return "ai-open-api" if "ai-open-api" in text else None


def resolve_route_args(args: list[str]) -> list[str]:
    scope = ""
    service = ""
    explicit_project = ""
    explicit_logstore = ""
    query_text = ""
    keyword_text = ""
    pass_args: list[str] = []
    route_hint_present = query_present = keyword_present = trace_present = service_present = False

    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--scope":
            scope = normalize_scope(args[i + 1])
            route_hint_present = True
            i += 2
        elif arg == "--service":
            service = args[i + 1]
            service_present = True
            route_hint_present = True
            i += 2
        elif arg == "--project":
            explicit_project = args[i + 1]
            route_hint_present = True
            pass_args.extend([arg, args[i + 1]])
            i += 2
        elif arg == "--logstore":
            explicit_logstore = args[i + 1]
            pass_args.extend([arg, args[i + 1]])
            i += 2
        elif arg == "--query":
            query_text = args[i + 1]
            query_present = True
            pass_args.extend([arg, args[i + 1]])
            i += 2
        elif arg == "--keyword":
            keyword_text = args[i + 1]
            keyword_present = True
            pass_args.extend([arg, args[i + 1]])
            i += 2
        elif arg == "--trace-id":
            trace_present = True
            pass_args.extend([arg, args[i + 1]])
            i += 2
        else:
            pass_args.append(arg)
            i += 1

    if explicit_project:
        if not explicit_logstore:
            logstore = infer_logstore_by_project(explicit_project)
            if not logstore:
                raise SystemExit(f"[!] 已指定 --project={explicit_project}，但无法自动推断 logstore。\n    请补充 --logstore。")
            pass_args.extend(["--logstore", logstore])
        return pass_args

    inferred_service = infer_service_from_text(f"{query_text} {keyword_text}")
    if not service and inferred_service:
        service = inferred_service
        service_present = True

    route_project = route_logstore = ""
    if scope == "overseas":
        route_project, route_logstore = PROJECT_OVERSEAS, LOGSTORE_OVERSEAS
    elif service == "ai-open-api":
        route_project, route_logstore = PROJECT_CN_OPENAPI, LOGSTORE_CN_OPENAPI
    elif scope == "domestic" and service:
        route_project, route_logstore = PROJECT_CN_DEFAULT, LOGSTORE_CN_DEFAULT

    if not route_project:
        if not route_hint_present and not query_present and not keyword_present and not trace_present:
            return pass_args
        raise SystemExit(
            "[!] 无法确定要查询的 SLS project。\n"
            f"    1. 海外全部服务 -> {PROJECT_OVERSEAS} / {LOGSTORE_OVERSEAS}\n"
            f"    2. 国内 ai-open-api -> {PROJECT_CN_OPENAPI} / {LOGSTORE_CN_OPENAPI}\n"
            f"    3. 国内其他服务 -> {PROJECT_CN_DEFAULT} / {LOGSTORE_CN_DEFAULT}\n"
            "    请补充 --scope domestic|overseas、--service <service>，或直接指定 --project/--logstore。"
        )

    if service_present and not query_present:
        pass_args.extend(["--query", f"{service} | with_pack_meta"])
    pass_args.extend(["--project", route_project, "--logstore", route_logstore])
    print(f"[*] 路由 project/logstore: {route_project} / {route_logstore}", file=sys.stderr)
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


def run_query(args: list[str]) -> int:
    runtime, _, query_script = runtime_and_paths()
    if wants_help(args):
        result = run_subprocess([runtime["python_bin"], str(query_script), *args])
        print_output(result)
        return result.returncode

    require_credentials(runtime)
    session_file = Path(runtime["sls_dir"]) / "aliyun_session.json"
    if not session_file.exists():
        print("[*] 未找到 session，先执行登录")
        code = run_login_auto()
        if code != 0:
            return code

    resolved_args = resolve_route_args(args)
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


AGG_USAGE = """sls agg — pSql 聚合统计 (count / GROUP BY / HAVING / 精确去重总数)

  sls agg --service <svc> --sql 'SELECT ...' [--index '*'] [--minutes 60|--from F --to T] [--size 100] [--out FILE]
  sls agg --scope domestic --service call-task-number --sql 'SELECT count(*) as c' --minutes 60
  sls agg --project P --logstore L --index 'svc and "ERR"' --sql 'SELECT count(*) as c' --from F --to T

参数:
  --sql     必选, 管道后的 SQL (不要带 index 查询部分)
  --index   管道前的 SLS 查询, 默认取 --service 名, 再不然 *
  --scope   domestic|overseas (路由 project/logstore)
  --service 服务名 (路由 + 默认 index)
  --project/--logstore  直接指定, 覆盖 scope/service 推断
  --from/--to  Unix 秒; 不带则用 --minutes (默认 60)
  --size    聚合结果行数, 1-100, 默认 100 (SLS 服务端硬上限 100)
  --out     落盘 JSON

避坑 (详见 references/advanced-query.md):
  - 中文/纯数字过滤写进 SQL 的 like, 别塞 --index 的 "中文"/"606332"
  - 精确去重总数用 count(distinct case when ...), 不要配 GROUP BY
  - 分组先在 --index 限定日志子集再 GROUP BY; 全表 GROUP BY 不稳
"""


def resolve_agg_args(args: list[str]) -> list[str]:
    """解析 sls agg 参数, 推断 project/logstore, 拼成底层 --psql --query 调用参数。"""
    parser = argparse.ArgumentParser(prog="sls agg", add_help=False, allow_abbrev=False)
    parser.add_argument("--sql")
    parser.add_argument("--index")
    parser.add_argument("--scope")
    parser.add_argument("--service", default="")
    parser.add_argument("--project")
    parser.add_argument("--logstore")
    parser.add_argument("--from", dest="from_ts", type=int)
    parser.add_argument("--to", dest="to_ts", type=int)
    parser.add_argument("--minutes", type=int, default=60)
    parser.add_argument("--size", type=int, default=100)
    parser.add_argument("--out")
    parsed = parser.parse_args(args)

    if not parsed.sql:
        raise SystemExit("[!] sls agg 需要 --sql 'SELECT ...' (管道后的 SQL)")
    if not 1 <= parsed.size <= 100:
        raise SystemExit("[!] sls agg 的 --size 必须在 1-100 之间；超过 100 请分时段查询后合并去重")

    scope = normalize_scope(parsed.scope) if parsed.scope else ""
    project = parsed.project
    logstore = parsed.logstore

    if project:
        if not logstore:
            logstore = infer_logstore_by_project(project)
            if not logstore:
                raise SystemExit(
                    f"[!] 已指定 --project={project}，但无法自动推断 logstore。\n"
                    "    请补充 --logstore。"
                )
    elif scope == "overseas":
        project = PROJECT_OVERSEAS
        logstore = logstore or LOGSTORE_OVERSEAS
    elif parsed.service == "ai-open-api":
        project = PROJECT_CN_OPENAPI
        logstore = logstore or LOGSTORE_CN_OPENAPI
    else:
        project = PROJECT_CN_DEFAULT
        logstore = logstore or LOGSTORE_CN_DEFAULT

    index = parsed.index if parsed.index is not None else parsed.service or "*"

    out_args = ["--psql", "--query", f"{index} | {parsed.sql}", "--size", str(parsed.size)]
    if parsed.from_ts is not None:
        out_args += ["--from", str(parsed.from_ts)]
    if parsed.to_ts is not None:
        out_args += ["--to", str(parsed.to_ts)]
    if parsed.from_ts is None and parsed.to_ts is None:
        out_args += ["--minutes", str(parsed.minutes)]
    out_args += ["--project", project, "--logstore", logstore]
    if parsed.out:
        out_args += ["--out", parsed.out]
    return out_args


def run_agg(args: list[str]) -> int:
    runtime, _, query_script = runtime_and_paths()
    if wants_help(args) or not args:
        print(AGG_USAGE, end="")
        return 0

    resolved_args = resolve_agg_args(args)
    require_credentials(runtime)
    session_file = Path(runtime["sls_dir"]) / "aliyun_session.json"
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
    if cmd == "query":
        return run_query(args)
    if cmd == "agg":
        return run_agg(args)
    print(f"[!] 未知子命令: {cmd}")
    print(usage(), end="")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
