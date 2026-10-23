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


SESSION_ERROR_RE = re.compile(r"ConsoleNeedLogin|Invalid CSRF token|找不到 session 文件")
SAFE_MAX_MINUTES = 3 * 24 * 60
SAFE_MAX_SIZE = 100
SAFE_AGG_RE = re.compile(r"\b(count|sum|avg|min|max|approx_distinct)\s*\(", re.I)
SAFE_AGG_EXPRESSION_RE = re.compile(
    r"^(count|sum|avg|min|max|approx_distinct)\s*\(.*\)$", re.I | re.S
)
CATCH_ALL_REGEX_RE = re.compile(
    r"(?<!\\)\.\s*(?:[*+]\??|\{\s*\d*\s*,)|"
    r"\[\s*\\s\s*\\S\s*\]\s*[*+]\??",
    re.I,
)
RAW_LOG_FIELD_RE = re.compile(
    r"(?<![\w$])(?:[`\"]?)(content|message|__content__)(?:[`\"]?)(?![\w$])",
    re.I,
)
DIRECT_RAW_LOG_FIELD_RE = re.compile(
    r"^(?:(?:[`\"]?[A-Za-z_][\w$]*[`\"]?)\.)?"
    r"[`\"]?(?:content|message|__content__)[`\"]?$",
    re.I,
)


def usage() -> str:
    return """用法:
  sls login [--keep]
  sls query --service ai-open-api [--size 100]
  sls query --scope domestic --service data-center --keyword ERROR
  sls query --scope overseas --service agentcc --minutes 60
  sls query --project <project> [--logstore <logstore>] --query '*'
  sls agg --service <svc> --sql 'SELECT count(*) as c' [--minutes 60|--from F --to T] [--size 100] [--out FILE]
  sls search-safe --scope domestic --service data-center --trace-id <id>
  sls agg-safe --scope domestic --service data-center --sql 'SELECT count(*) as c'

说明:
  - Python / AGENT_TOOLKIT_ROOT / TMP_DIR 来自 work 运行时配置
  - 未初始化时请先运行: python3 $AGENT_TOOLKIT_ROOT/common/bootstrap_work.py
  - 底层脚本来自 $AGENT_TOOLKIT_ROOT/skills/aliyun-sls-log-query/scripts
  - 凭证与 session 目录来自 work 运行时配置里的 sls_dir
  - login 默认在抓取 session 后关闭浏览器，仅 --keep 保留窗口
  - query 会在 session 缺失或失效时自动转去登录，再重试一次
  - search-safe/agg-safe 禁止自选 project/logstore、完整输出和落盘，并限制窗口与结果量
  - agg 走 pSql 聚合(count / GROUP BY / 精确去重总数), 使用 --index + --sql, 避坑见 references/advanced-query.md
  - project/logstore 路由从本机工作配置的 sls 节读取，支持国内/海外默认目标和服务覆盖
  - 默认配置: ~/.local/agent-toolkit/work/config.json，可用 AGENT_TOOLKIT_WORK_CONFIG 覆盖路径
  - 初始化空模板: python3 $AGENT_TOOLKIT_ROOT/scripts/init_work_config.py
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


def load_sls_config(*, required: bool = True) -> dict:
    try:
        config = load_work_config(required=required)
    except WorkConfigError as exc:
        raise SystemExit(f"[!] {exc}") from exc
    sls = config.get("sls", {})
    if not isinstance(sls, dict) or (required and not sls):
        raise SystemExit("[!] 工作配置需要 sls 路由；请运行 scripts/init_work_config.py 并填写本机配置。")
    return sls


def scope_routes(config: dict, scope: str) -> tuple[dict, dict]:
    scope_config = config.get(scope, {})
    if not isinstance(scope_config, dict):
        raise SystemExit(f"[!] 工作配置 sls.{scope} 必须是对象。")
    services = scope_config.get("services", {})
    if not isinstance(services, dict):
        raise SystemExit(f"[!] 工作配置 sls.{scope}.services 必须是对象。")
    return scope_config, services


def route_target(route: object, label: str, *, required: bool = True) -> tuple[str, str] | None:
    if not isinstance(route, dict):
        raise SystemExit(f"[!] 工作配置 {label} 必须是包含 project/logstore 的对象。")
    values = [route.get(name, "") for name in ("project", "logstore")]
    if not all(isinstance(value, str) for value in values):
        raise SystemExit(f"[!] 工作配置 {label} 的 project/logstore 必须是字符串。")
    project, logstore = (value.strip() for value in values)
    if not project or not logstore:
        if required:
            raise SystemExit(f"[!] 工作配置 {label} 需要非空 project 和 logstore。")
        return None
    return project, logstore


def configured_route(config: dict, scope: str, service: str = "") -> tuple[str, str]:
    scope_config, services = scope_routes(config, scope)
    label = f"sls.{scope}"
    if service in services:
        route = route_target(services[service], f"{label}.services.{service}")
    else:
        route = route_target(scope_config.get("default", {}), f"{label}.default")
    assert route is not None
    return route


def infer_logstore_by_project(project: str) -> str | None:
    logstores = set()
    config = load_sls_config(required=False)
    for scope in ("domestic", "overseas"):
        scope_config, services = scope_routes(config, scope)
        routes = [("default", scope_config.get("default", {})), *services.items()]
        for name, route in routes:
            target = route_target(route, f"sls.{scope}.{name}", required=False)
            if target is not None and target[0] == project:
                logstores.add(target[1])
    return next(iter(logstores)) if len(logstores) == 1 else None


def infer_service_from_text(text: str, config: dict, scope: str) -> str | None:
    _, services = scope_routes(config, scope)
    matches = [name for name in services if name and name in text]
    if len(matches) > 1:
        raise SystemExit("[!] 查询包含多个已配置服务；请显式指定 --service。")
    return matches[0] if matches else None


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

    config = load_sls_config()
    if not service:
        inferred_service = infer_service_from_text(
            f"{query_text} {keyword_text}", config, scope or "domestic"
        )
        if inferred_service:
            service = inferred_service
            service_present = True

    _, services = scope_routes(config, scope or "domestic")
    if scope == "overseas":
        route_project, route_logstore = configured_route(config, scope, service)
    elif (scope == "domestic" and service) or service in services:
        route_project, route_logstore = configured_route(config, "domestic", service)
    elif not route_hint_present and not query_present and not keyword_present and not trace_present:
        route_project, route_logstore = configured_route(config, "domestic")
    else:
        raise SystemExit(
            "[!] 无法确定要查询的 SLS project。\n"
            "    请补充 --scope domestic|overseas、--service <service>，或直接指定 --project/--logstore。"
        )

    if service_present and not query_present:
        pass_args.extend(["--query", f"{service} | with_pack_meta"])
    pass_args.extend(["--project", route_project, "--logstore", route_logstore])
    print(f"[*] 路由 project/logstore: {route_project} / {route_logstore}", file=sys.stderr)
    return pass_args


def add_safe_route_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--scope", choices=["domestic", "overseas"])
    parser.add_argument("--service", required=True)
    parser.add_argument("--minutes", type=int, default=60)
    parser.add_argument("--from", dest="from_ts", type=int)
    parser.add_argument("--to", dest="to_ts", type=int)
    parser.add_argument("--size", type=int, default=20)


def validate_safe_window(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if not 1 <= args.size <= SAFE_MAX_SIZE:
        parser.error(f"--size 必须在 1-{SAFE_MAX_SIZE} 之间")
    if not 1 <= args.minutes <= SAFE_MAX_MINUTES:
        parser.error(f"--minutes 必须在 1-{SAFE_MAX_MINUTES} 之间")
    if (args.from_ts is None) != (args.to_ts is None):
        parser.error("--from 和 --to 必须同时提供")
    if args.from_ts is not None:
        if args.to_ts < args.from_ts:
            parser.error("--to 不能早于 --from")
        if args.to_ts - args.from_ts > SAFE_MAX_MINUTES * 60:
            parser.error(f"时间窗口不能超过 {SAFE_MAX_MINUTES} 分钟")


def safe_common_args(args: argparse.Namespace) -> list[str]:
    resolved = ["--service", args.service, "--size", str(args.size), "--redact"]
    if args.scope:
        resolved.extend(["--scope", args.scope])
    if args.from_ts is not None:
        resolved.extend(["--from", str(args.from_ts), "--to", str(args.to_ts)])
    else:
        resolved.extend(["--minutes", str(args.minutes)])
    return resolved


def split_top_level_sql(value: str, separator: str = ",") -> list[str]:
    """Split a small SQL fragment without treating quoted/nested separators as syntax."""
    parts: list[str] = []
    start = 0
    depth = 0
    quote = ""
    i = 0
    while i < len(value):
        char = value[i]
        if quote:
            if char == quote:
                if i + 1 < len(value) and value[i + 1] == quote:
                    i += 2
                    continue
                quote = ""
            elif char == "\\" and i + 1 < len(value):
                i += 2
                continue
        elif char in {"'", '"', "`"}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == separator and depth == 0:
            parts.append(value[start:i].strip())
            start = i + 1
        i += 1
    parts.append(value[start:].strip())
    return [part for part in parts if part]


def find_top_level_sql_clause(sql: str, clauses: tuple[str, ...], start: int = 0) -> tuple[int, str]:
    depth = 0
    quote = ""
    i = start
    while i < len(sql):
        char = sql[i]
        if quote:
            if char == quote:
                if i + 1 < len(sql) and sql[i + 1] == quote:
                    i += 2
                    continue
                quote = ""
            elif char == "\\" and i + 1 < len(sql):
                i += 2
                continue
        elif char in {"'", '"', "`"}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif depth == 0:
            for clause in clauses:
                end = i + len(clause)
                if sql[i:end].lower() == clause and (
                    i == 0 or not (sql[i - 1].isalnum() or sql[i - 1] == "_")
                ) and (
                    end == len(sql) or not (sql[end].isalnum() or sql[end] == "_")
                ):
                    return i, clause
        i += 1
    return len(sql), ""


def strip_sql_alias(expression: str) -> str:
    match = re.match(
        r"^(.*?)(?:\s+as\s+|\s+)(?:[`\"]?[A-Za-z_][\w$]*[`\"]?)\s*$",
        expression,
        re.I | re.S,
    )
    return match.group(1).strip() if match else expression.strip()


def validate_safe_regexp_dimension(
    parser: argparse.ArgumentParser, expression: str
) -> bool:
    match = re.match(r"^regexp_extract\s*\((.*)\)$", expression, re.I | re.S)
    if not match:
        return False
    arguments = split_top_level_sql(match.group(1))
    if len(arguments) != 3 or not DIRECT_RAW_LOG_FIELD_RE.fullmatch(arguments[0]):
        parser.error("--sql 的 regexp_extract 只允许从明确的原始日志字段派生维度")
    pattern_literal = arguments[1].strip()
    if (
        len(pattern_literal) < 2
        or pattern_literal[0] not in {"'", '"'}
        or pattern_literal[-1] != pattern_literal[0]
        or not re.fullmatch(r"[1-9]\d*", arguments[2].strip())
    ):
        parser.error("--sql 的 regexp_extract 必须使用字符串模式和正整数捕获组")
    pattern = pattern_literal[1:-1]
    if len(pattern) > 256 or CATCH_ALL_REGEX_RE.search(pattern):
        parser.error("--sql 的 regexp_extract 不允许无边界捕获完整日志")
    return True


def validate_safe_aggregate_sql(parser: argparse.ArgumentParser, sql: str) -> None:
    select_start = re.match(r"^select\b", sql, re.I)
    if not select_start:
        parser.error("--sql 只允许 SELECT 聚合查询")
    if any(token in sql for token in (";", "--", "/*", "*/")):
        parser.error("--sql 不允许多语句或注释")
    if not SAFE_AGG_RE.search(sql):
        parser.error("--sql 必须包含 count/sum/avg/min/max 等聚合函数")

    clause_names = ("from", "where", "group by", "having", "order by", "limit")
    select_end, _ = find_top_level_sql_clause(sql, clause_names, select_start.end())
    select_items = split_top_level_sql(sql[select_start.end():select_end])
    if not select_items:
        parser.error("--sql 必须包含聚合结果列")

    for item in select_items:
        expression = strip_sql_alias(item)
        if DIRECT_RAW_LOG_FIELD_RE.fullmatch(expression):
            parser.error("--sql 不允许直接输出 content/message/__content__ 原始日志字段")
        aggregate = SAFE_AGG_EXPRESSION_RE.fullmatch(expression)
        if RAW_LOG_FIELD_RE.search(expression):
            if aggregate:
                if aggregate.group(1).lower() in {"min", "max"}:
                    parser.error("--sql 不允许通过 min/max 输出原始日志字段")
            elif not validate_safe_regexp_dimension(parser, expression):
                parser.error(
                    "--sql 中的原始日志字段只能用于完整聚合表达式或 regexp_extract 派生维度"
                )

    group_start, group_clause = find_top_level_sql_clause(sql, ("group by",), select_end)
    if group_clause:
        group_value_start = group_start + len(group_clause)
        group_end, _ = find_top_level_sql_clause(
            sql, ("having", "order by", "limit"), group_value_start
        )
        for item in split_top_level_sql(sql[group_value_start:group_end]):
            if DIRECT_RAW_LOG_FIELD_RE.fullmatch(item.strip()):
                parser.error("--sql 不允许按 content/message/__content__ 原始日志字段分组")


def resolve_search_safe_args(argv: list[str]) -> list[str]:
    parser = argparse.ArgumentParser(prog="sls search-safe", allow_abbrev=False)
    add_safe_route_args(parser)
    filters = parser.add_mutually_exclusive_group()
    filters.add_argument("--trace-id")
    filters.add_argument("--keyword")
    filters.add_argument("--query")
    args = parser.parse_args(argv)
    validate_safe_window(parser, args)
    resolved = safe_common_args(args)
    for name in ("trace_id", "keyword", "query"):
        value = getattr(args, name)
        if value is not None:
            resolved.extend(["--" + name.replace("_", "-"), value])
    return resolved


def resolve_agg_safe_args(argv: list[str]) -> list[str]:
    parser = argparse.ArgumentParser(prog="sls agg-safe", allow_abbrev=False)
    add_safe_route_args(parser)
    parser.set_defaults(size=100)
    parser.add_argument("--sql", required=True)
    parser.add_argument("--index")
    args = parser.parse_args(argv)
    validate_safe_window(parser, args)
    sql = args.sql.strip()
    validate_safe_aggregate_sql(parser, sql)
    resolved = safe_common_args(args)
    resolved.extend(["--sql", sql])
    if args.index is not None:
        resolved.extend(["--index", args.index])
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


def run_query(args: list[str]) -> int:
    if wants_help(args):
        runtime, _, query_script = runtime_and_paths()
        result = run_subprocess([runtime["python_bin"], str(query_script), *args])
        print_output(result)
        return result.returncode

    resolved_args = resolve_route_args(args)
    runtime, _, query_script = runtime_and_paths()
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
    parser.add_argument("--redact", action="store_true")
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
    else:
        project, configured_logstore = configured_route(load_sls_config(), scope or "domestic", parsed.service)
        logstore = logstore or configured_logstore

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
    if parsed.redact:
        out_args += ["--redact"]
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
    if cmd == "search-safe":
        return run_query(resolve_search_safe_args(args))
    if cmd == "agg-safe":
        return run_agg(resolve_agg_safe_args(args))
    print(f"[!] 未知子命令: {cmd}")
    print(usage(), end="")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
