#!/usr/bin/env python3
"""Read-only GET for the work Nacos HTTP wrapper."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request


COMMON_DIR = Path(__file__).resolve().parents[3] / "common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from runtime import RuntimeConfigError, load_runtime_or_exit, normalize_http_base_url


DEFAULT_TIMEOUT_SECONDS = 20
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_PRINT_CHARS = 24_000
MAX_HITS = 200
DEFAULT_GROUP = "DEFAULT_GROUP"
LANGUAGE_GROUP = "LANGUAGE_GROUP"
SCOPE_KEYS = {"cn": "nacos_cn_base_url", "os": "nacos_os_base_url"}
# Live dataId has no suffix and the body is JSON.
BARE_SERVICES = {"ai-quartz"}
LANG_PREFIXES = ("common-lang", "ai-lang")
LOCALE_ALIASES = {
    "zh": "zh_CN",
    "zh-cn": "zh_CN",
    "zh_cn": "zh_CN",
    "cn": "zh_CN",
    "id": "in",
    "idn": "in",
    "indonesian": "in",
}
SENSITIVE_KEY_RE = re.compile(
    r"(secret|password|passwd|pwd|token|api[_-]?key|access[_-]?key|private[_-]?key|"
    r"app[_-]?key|app[_-]?secret|server[_-]?key|server[_-]?secret|license|credential)",
    re.I,
)
LINE_KV_RE = re.compile(r"^(\s*(?:-\s+)?)([A-Za-z0-9_.-]+)(\s*[:=]\s*)(.*)$")
LANG_FILE_RE = re.compile(r"^(common-lang|ai-lang)\.([^.]+)\.properties$", re.I)
BLOCK_SCALAR_RE = re.compile(r"^[|>][+-]?$")
INLINE_SECRET_RE = re.compile(
    r"""(?i)(["']?)(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|app[_-]?key|app[_-]?secret|credential)\1(\s*[:=]\s*)(["']?)([^"'\s,}&]+)\4"""
)
URI_SECRET_RE = re.compile(
    r"(?i)((?:password|passwd|pwd|token|secret|access[_-]?key|api[_-]?key)\s*=\s*)([^&\s]+)"
)
DEBUGGER_RE = re.compile(r'class="errormsg">(.*?)</p>', re.S)
TAG_RE = re.compile(r"<[^>]+>")


class QueryError(RuntimeError):
    """User-facing validation or HTTP error."""


def normalize_base_url(value: str, field_name: str) -> str:
    try:
        return normalize_http_base_url(value, field_name)
    except RuntimeConfigError as exc:
        raise QueryError(str(exc)) from exc


def resolve_bases(scope: str, runtime: dict[str, Any]) -> list[tuple[str, str]]:
    wanted = ("cn", "os") if scope == "both" else (scope,)
    out: list[tuple[str, str]] = []
    missing: list[str] = []
    toolkit_root = Path(str(runtime.get("agent_toolkit_root") or ""))
    for name in wanted:
        field = SCOPE_KEYS[name]
        raw = str(runtime.get(field) or "").strip()
        if not raw:
            missing.append(field)
            continue
        out.append((name, normalize_base_url(raw, field)))
    if missing:
        bootstrap = toolkit_root / "common" / "bootstrap_work.py"
        raise QueryError(
            "work 运行时配置缺少 "
            + ", ".join(missing)
            + f"；请交互运行 {bootstrap}"
        )
    return out


def http_get(url: str) -> tuple[int, str, str]:
    req = urllib_request.Request(
        url,
        headers={"User-Agent": "agent-toolkit-nacos-config-query/1"},
        method="GET",
    )
    try:
        with urllib_request.urlopen(req, timeout=DEFAULT_TIMEOUT_SECONDS) as resp:
            raw = resp.read(MAX_RESPONSE_BYTES + 1)
            status = getattr(resp, "status", 200) or 200
            ctype = resp.headers.get("content-type") or ""
    except urllib_error.HTTPError as exc:
        raw = exc.read(MAX_RESPONSE_BYTES + 1)
        status = exc.code
        ctype = exc.headers.get("content-type") or ""
    except urllib_error.URLError as exc:
        raise QueryError(f"请求失败 {url}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise QueryError(f"超时 {url}") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise QueryError(f"响应超过 {MAX_RESPONSE_BYTES} bytes: {url}")
    return status, ctype, raw.decode("utf-8", "replace")


def debugger_message(html: str) -> str | None:
    if "Werkzeug Debugger" not in html and "KeyError" not in html and "yaml.scan" not in html:
        return None
    match = DEBUGGER_RE.search(html)
    if not match:
        return "wrapper 500 (Werkzeug Debugger)"
    return TAG_RE.sub("", match.group(1)).replace("&#39;", "'").strip()


def _strip_known_suffix(name: str) -> str:
    lower = name.lower()
    for suffix in (".yaml", ".yml", ".properties"):
        if lower.endswith(suffix):
            return name[: -len(suffix)]
    return name


def locale_to_suffix(locale: str) -> str:
    raw = (locale or "en").strip()
    return LOCALE_ALIASES.get(raw.lower(), raw)


def normalize_lang_data_id(name: str) -> str | None:
    match = LANG_FILE_RE.match(name.strip())
    if not match:
        return None
    prefix, locale = match.group(1).lower(), match.group(2)
    return f"{prefix}.{locale_to_suffix(locale)}.properties"


def canonicalize_data_id(raw: str, *, from_service: bool) -> str:
    name = (raw or "").strip()
    if not name:
        raise QueryError("service 或 data-id 不能为空")
    lang_data_id = normalize_lang_data_id(name)
    if lang_data_id:
        return lang_data_id
    if not from_service:
        return name
    if name.lower().endswith(".properties"):
        return name
    stem = _strip_known_suffix(name).lower().replace("_", "-")
    if stem in BARE_SERVICES:
        return stem
    return stem + ".yaml"


def service_to_data_id(service: str) -> str:
    return canonicalize_data_id(service, from_service=True)


def exact_data_id(data_id: str) -> str:
    return canonicalize_data_id(data_id, from_service=False)


def infer_group(data_id: str, group: str | None) -> str:
    if group:
        return group.strip()
    lower = data_id.lower()
    if lower.endswith(".properties") and any(
        lower.startswith(f"{prefix}.") for prefix in LANG_PREFIXES
    ):
        return LANGUAGE_GROUP
    return DEFAULT_GROUP


def config_url(base: str, data_id: str, group: str) -> str:
    path = "/config/" + urllib_parse.quote(data_id, safe="")
    url = base + path
    if group and group != DEFAULT_GROUP:
        url += "?" + urllib_parse.urlencode({"group": group})
    return url


def redact_inline(text: str) -> str:
    text = URI_SECRET_RE.sub(r"\1***", text)
    return INLINE_SECRET_RE.sub(r"\2\3***", text)


def redact_value(key: str, value: Any) -> Any:
    if key and SENSITIVE_KEY_RE.search(key):
        return "***"
    if isinstance(value, dict):
        return {str(k): redact_value(str(k), v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_value("", item) for item in value]
    if isinstance(value, str):
        return redact_text(value) if "\n" in value else redact_inline(value)
    return value


def redact_text(text: str) -> str:
    stripped = text.lstrip()
    if stripped[:1] in "{[":
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, (dict, list)):
            return json.dumps(redact_value("", parsed), ensure_ascii=False, indent=2)
    out: list[str] = []
    mask_deeper_than: int | None = None
    for line in text.splitlines():
        indent = len(line) - len(line.lstrip(" "))
        if mask_deeper_than is not None:
            if line.strip() and indent <= mask_deeper_than:
                mask_deeper_than = None
            else:
                out.append(f"{line[:indent]}***" if line.strip() else line)
                continue
        match = LINE_KV_RE.match(line)
        if match and SENSITIVE_KEY_RE.search(match.group(2)):
            out.append(f"{match.group(1)}{match.group(2)}{match.group(3)}***")
            if BLOCK_SCALAR_RE.fullmatch(match.group(4).strip()):
                mask_deeper_than = indent
            continue
        out.append(redact_inline(line))
    return "\n".join(out)


def filter_text(text: str, contains: str | None) -> tuple[str, int, bool]:
    if not contains:
        return text, 0, False
    needle = contains.lower()
    hits = [line for line in text.splitlines() if needle in line.lower()]
    truncated = len(hits) > MAX_HITS
    return "\n".join(hits[:MAX_HITS]), len(hits), truncated


def maybe_truncate(text: str) -> tuple[str, bool]:
    if len(text) <= MAX_PRINT_CHARS:
        return text, False
    return text[:MAX_PRINT_CHARS], True


def lang_data_id(locale: str, prefix: str) -> str:
    normalized = (prefix or "").strip().lower()
    if normalized not in LANG_PREFIXES:
        raise QueryError("lang --prefix 只接受 common-lang 或 ai-lang")
    return f"{normalized}.{locale_to_suffix(locale)}.properties"


def do_get(base: str, data_id: str, group: str, contains: str | None) -> dict[str, Any]:
    url = config_url(base, data_id, group)
    status, ctype, body = http_get(url)
    dbg = debugger_message(body)
    if dbg:
        return {
            "ok": False,
            "http_status": status,
            "url": url,
            "data_id": data_id,
            "group": group,
            "error": dbg,
        }
    if status == 404 or body.strip() == "Config not found":
        lower_data_id = data_id.lower()
        if (
            group != LANGUAGE_GROUP
            and lower_data_id.endswith(".properties")
            and any(lower_data_id.startswith(f"{prefix}.") for prefix in LANG_PREFIXES)
        ):
            hint = "翻译必须 group=LANGUAGE_GROUP。"
        elif data_id == "autodialer.yaml":
            hint = "国内 autodialer 实际是 autodialer8.yaml，海外是 autodialer.yaml。404 不要改搜其他文件名。"
        else:
            hint = "404 表示这份包装里没有该 dataId。按精确 dataId 停止，不要改搜其他文件名。"
        return {
            "ok": False,
            "http_status": 404,
            "url": url,
            "data_id": data_id,
            "group": group,
            "error": "Config not found",
            "hint": hint,
        }
    if status >= 400:
        return {
            "ok": False,
            "http_status": status,
            "url": url,
            "data_id": data_id,
            "group": group,
            "error": redact_text(body[:300]),
        }
    redacted = redact_text(body)
    filtered, hit_count, hits_truncated = filter_text(redacted, contains)
    printed, truncated = maybe_truncate(filtered if contains else redacted)
    return {
        "ok": True,
        "http_status": status,
        "url": url,
        "data_id": data_id,
        "group": group,
        "content_type": ctype,
        "bytes": len(body.encode("utf-8")),
        "redacted": True,
        "contains": contains,
        "hit_count": hit_count if contains else None,
        "hits_truncated": hits_truncated if contains else False,
        "truncated": truncated,
        "content": printed,
    }


def wrap_scope(scope_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"scope": scope_name, **payload}


def run_per_scope(scope: str, fn, runtime: dict[str, Any]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for name, base in resolve_bases(scope, runtime):
        try:
            payload = fn(base)
        except QueryError as exc:
            payload = {"ok": False, "error": str(exc)}
        items.append(wrap_scope(name, payload))
    if len(items) == 1:
        return items[0]
    return {"scope": "both", "results": items}


def result_ok(result: dict[str, Any]) -> bool:
    results = result.get("results")
    if isinstance(results, list):
        return all(bool(item.get("ok")) for item in results)
    return bool(result.get("ok"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="GET work Nacos config by exact dataId (read-only)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--scope", choices=("cn", "os", "both"), default="cn")

    p_get = sub.add_parser("get", help="按服务名或精确 dataId 读取配置")
    add_common(p_get)
    p_get.add_argument("--service", default=None, help="spring.application.name；无后缀时补 .yaml")
    p_get.add_argument("--data-id", default=None, help="精确 dataId，覆盖 --service")
    p_get.add_argument("--group", default=None, help="默认 DEFAULT_GROUP；翻译 properties 推断 LANGUAGE_GROUP")
    p_get.add_argument("--contains", default=None, help="只输出含该字串的行")

    p_lang = sub.add_parser("lang", help="LANGUAGE_GROUP 翻译文件")
    add_common(p_lang)
    p_lang.add_argument("--locale", default="en")
    p_lang.add_argument("--prefix", default="common-lang", help="common-lang 或 ai-lang")
    p_lang.add_argument("--contains", default=None)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        runtime = load_runtime_or_exit(__file__)
        if args.command == "lang":
            data_id = lang_data_id(args.locale, args.prefix)
            group = LANGUAGE_GROUP
            contains = args.contains
        else:
            if args.data_id:
                data_id = exact_data_id(args.data_id)
            elif args.service:
                data_id = service_to_data_id(args.service)
            else:
                raise QueryError("get 需要 --service 或 --data-id")
            group = infer_group(data_id, args.group)
            contains = args.contains

        def _get(base: str, data_id: str = data_id, group: str = group, contains: str | None = contains) -> dict[str, Any]:
            return do_get(base, data_id, group, contains)

        result = run_per_scope(args.scope, _get, runtime)
    except QueryError as exc:
        print(f"[!] {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result_ok(result) else 1


if __name__ == "__main__":
    raise SystemExit(main())
