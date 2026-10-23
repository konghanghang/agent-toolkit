"""Aliyun SLS 日志查询脚本 - 用运行时配置里的 SLS_DIR/aliyun_session.json 直接 POST 取日志, 无需浏览器。

前置: 先运行 aliyun_login.py 完成登录并抓 session。

用法:
    PYTHON_BIN aliyun_sls_query.py                                # 默认查最近 15 分钟
    PYTHON_BIN aliyun_sls_query.py --minutes 60 --size 50
    PYTHON_BIN aliyun_sls_query.py --query 'ERROR and data-center'
    PYTHON_BIN aliyun_sls_query.py --trace-id 6b62e9b764de5d45
    PYTHON_BIN aliyun_sls_query.py --out logs.json
"""
from __future__ import annotations

import argparse
import json
import re
import secrets
import sys
import time
from pathlib import Path

import requests

COMMON_DIR = Path(__file__).resolve().parents[3] / "common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from private_files import (
    PrivatePathError,
    secure_private_directory,
    secure_private_file,
)
from runtime import load_runtime_or_exit

GET_LOGS_URL = "https://sls.console.aliyun.com/console/logs/getLogs.json"
SENSITIVE_VALUE_RE = re.compile(
    r"(?i)((?:\\?[\"'])?(?:authorization|proxy-authorization|cookie|set-cookie|"
    r"x-auth-token|x-api-key|password|passwd|secret)(?:\\?[\"'])?\s*[:=]\s*(?:\\?[\"'])?)"
    r"([^\"'\r\n,}&]+)"
)
PHONE_RE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
EMAIL_RE = re.compile(
    r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![\w.-])"
)


def redact_sensitive_output(text: str) -> str:
    text = SENSITIVE_VALUE_RE.sub(r"\1***", text)
    text = PHONE_RE.sub("1**********", text)
    return EMAIL_RE.sub("***@***", text)


def storage_paths() -> tuple[Path, Path]:
    runtime = load_runtime_or_exit(__file__)
    base = Path(runtime["sls_dir"])
    return base / "aliyun-credentials.json", base / "aliyun_session.json"


def load_creds() -> dict:
    cred_file, _ = storage_paths()
    if not cred_file.exists():
        sys.exit(f"[!] 凭证文件不存在: {cred_file}")
    try:
        secure_private_directory(cred_file.parent)
        secure_private_file(cred_file)
    except PrivatePathError as exc:
        sys.exit(f"[!] 无法保护凭证文件: {exc}")
    return json.loads(cred_file.read_text(encoding="utf-8"))


def load_session() -> dict:
    _, session_file = storage_paths()
    if not session_file.exists():
        sys.exit(
            f"[!] 找不到 session 文件: {session_file}\n请先运行 aliyun_login.py 完成登录"
        )
    try:
        secure_private_directory(session_file.parent)
        secure_private_file(session_file)
    except PrivatePathError as exc:
        sys.exit(f"[!] 无法保护 session 文件: {exc}")
    return json.loads(session_file.read_text(encoding="utf-8"))


def gen_trace_ids() -> tuple[str, str, str]:
    trace = secrets.token_hex(16)
    span = secrets.token_hex(8)
    return (
        f"{trace}-{span}-1",
        f"00-{trace}-{span}-01",
        f"{trace}:{span}:0:1",
    )


def compose_query(
    base_query: str | None,
    *,
    trace_id: str | None = None,
    keyword: str | None = None,
) -> str:
    query = (base_query or "").strip()
    pipe_suffix = ""

    if "|" in query:
        prefix, suffix = query.split("|", 1)
        query = prefix.strip()
        pipe_suffix = f" | {suffix.strip()}"

    filters = []
    if query and query != "*":
        filters.append(query)
    if trace_id:
        filters.append(f"\"{trace_id}\"")
    if keyword:
        filters.append(keyword)

    if filters:
        return " and ".join(filters) + pipe_suffix
    if query == "*":
        return "*" + pipe_suffix
    return "*" + pipe_suffix


def query_logs(
    session: dict,
    creds: dict,
    *,
    minutes: int = 15,
    query: str | None = None,
    size: int = 20,
    project: str | None = None,
    logstore: str | None = None,
    from_ts: int | None = None,
    to_ts: int | None = None,
    psql: bool = False,
    redact: bool = False,
) -> dict:
    project = project or creds.get("project", "")
    logstore = logstore or creds.get("logstore", "")
    query = query or creds.get("default_query", "*")
    if not all(isinstance(value, str) and value.strip() for value in (project, logstore)):
        raise SystemExit("[!] 缺少 SLS project/logstore；请在私有配置中填写或显式指定")

    now = int(time.time())
    body = {
        "ProjectName": project,
        "LogStoreName": logstore,
        "from": from_ts if from_ts is not None else now - minutes * 60,
        "to": to_ts if to_ts is not None else now,
        "query": query,
        "Page": 1,
        "Size": size,
        "Reverse": "true",
        "pSql": "true" if psql else "false",
        "fullComplete": "false",
        "schemaFree": "false",
        "needHighlight": "true",
    }

    b3, traceparent, uber = gen_trace_ids()
    headers = {
        "accept": "application/json",
        "accept-language": "zh-CN,zh;q=0.9",
        "b3": b3,
        "bx-v": "2.5.36",
        "content-type": "application/x-www-form-urlencoded",
        "origin": "https://sls.console.aliyun.com",
        "priority": "u=1, i",
        "referer": f"https://sls.console.aliyun.com/lognext/project/{project}/logsearch/{logstore}",
        "sec-ch-ua": '"Google Chrome";v="149", "Chromium";v="149", "Not)A;Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "traceparent": traceparent,
        "uber-trace-id": uber,
        "user-agent": session.get(
            "user_agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
        ),
        "x-csrf-token": session.get("csrf", ""),
    }

    resp = requests.post(
        GET_LOGS_URL,
        data=body,
        headers=headers,
        cookies=session.get("cookies", {}),
        timeout=30,
    )
    print(f"[*] POST {GET_LOGS_URL} -> {resp.status_code}")
    if resp.status_code != 200:
        response_text = redact_sensitive_output(resp.text) if redact else resp.text
        print(f"[!] 响应: {response_text[:500]}")
        resp.raise_for_status()
    return resp.json()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=int, default=15, help="查询时间窗口(分钟)")
    parser.add_argument("--from", dest="from_ts", type=int, help="起始 Unix 时间戳(秒)")
    parser.add_argument("--to", dest="to_ts", type=int, help="结束 Unix 时间戳(秒)")
    parser.add_argument("--query", default=None, help="SLS 查询语句, 默认读 credentials")
    parser.add_argument("--trace-id", default=None, help="按 trace ID 过滤 (等价 --query)")
    parser.add_argument("--keyword", default=None, help="关键词 (等价 --query 'KW')")
    parser.add_argument("--size", type=int, default=20, help="返回条数")
    parser.add_argument("--project", default=None)
    parser.add_argument("--logstore", default=None)
    parser.add_argument("--out", help="保存原始 JSON 到该文件")
    parser.add_argument("--full", action="store_true", help="打印完整日志内容(不截断)")
    parser.add_argument("--psql", action="store_true", help="pSql 聚合模式, --query 写成 'index 查询 | SQL'")
    parser.add_argument("--redact", action="store_true", help="输出时脱敏认证信息、手机号和邮箱")
    args = parser.parse_args()
    load_runtime_or_exit(__file__)
    creds = load_creds()

    q = compose_query(
        args.query or creds.get("default_query"),
        trace_id=args.trace_id,
        keyword=args.keyword,
    )

    session = load_session()
    print(
        f"[*] Session: csrf_present={bool(session.get('csrf'))}, "
        f"cookies={len(session.get('cookies', {}))}, "
        f"age={int(time.time() - session.get('saved_at', time.time()))}s"
    )

    data = query_logs(
        session, creds,
        minutes=args.minutes,
        query=q,
        size=args.size,
        project=args.project,
        logstore=args.logstore,
        from_ts=args.from_ts,
        to_ts=args.to_ts,
        psql=args.psql,
        redact=args.redact,
    )

    if args.out:
        serialized = json.dumps(data, ensure_ascii=False, indent=2)
        if args.redact:
            serialized = redact_sensitive_output(serialized)
        Path(args.out).write_text(
            serialized, encoding="utf-8"
        )
        label = "脱敏响应" if args.redact else "完整响应"
        print(f"[+] {label}已写入 {args.out}\n")

    if isinstance(data, dict):
        print(f"[*] 响应 keys: {list(data.keys())}")
        if data.get("success") is False:
            code = data.get("code", "UnknownError")
            message = str(data.get("message", ""))
            if args.redact:
                message = redact_sensitive_output(message)
            print(f"[!] SLS 查询失败: code={code}, message={message}", file=sys.stderr)
            return 2
        # 兼容多种返回字段名
        logs = data.get("data") or data.get("logs") or data.get("items") or []
        if args.psql:
            print(f"[+] pSql 聚合结果 {len(logs)} 行:")
            for row in logs:
                output = json.dumps(row, ensure_ascii=False)
                print(redact_sensitive_output(output) if args.redact else output)
            return 0
        print(f"[+] 拿到 {len(logs)} 条日志\n")
        for i, log in enumerate(logs):
            ts = log.get("__time__") or log.get("time") or "?"
            content = (
                log.get("content")
                or log.get("message")
                or log.get("__content__")
                or json.dumps(log, ensure_ascii=False)
            )
            if isinstance(content, str) and not args.full:
                content = content[:300]
            if args.redact:
                content = redact_sensitive_output(str(content))
            print(f"[{i}] {ts} {content}")
    else:
        output = json.dumps(data, ensure_ascii=False, indent=2)
        if args.redact:
            output = redact_sensitive_output(output)
        print(output[:2000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
