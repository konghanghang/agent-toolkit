#!/usr/bin/env python3
"""Trigger GitLab pipelines and report bounded execution results."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

import yaml


COMMON_DIR = Path(__file__).resolve().parents[3] / "common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from private_files import PrivatePathError, secure_private_directory, secure_private_file
from runtime import RuntimeConfigError, load_runtime, normalize_http_base_url


DEFAULT_REQUEST_TIMEOUT_SECONDS = 30
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
JOBS_PER_PAGE = 100
MAX_JOB_PAGES = 5
DEFAULT_MONITOR_INTERVAL_SECONDS = 15
MIN_MONITOR_INTERVAL_SECONDS = 5
MAX_MONITOR_INTERVAL_SECONDS = 300
DEFAULT_MONITOR_TIMEOUT_SECONDS = 30 * 60
MAX_MONITOR_TIMEOUT_SECONDS = 2 * 60 * 60
MONITOR_HEARTBEAT_SECONDS = 60
MAX_CONSECUTIVE_MONITOR_ERRORS = 3
PROJECT_SEARCH_LIMIT = 20
TRIGGER_PLAN_VERSION = 1
PROD_ENVIRONMENTS = frozenset({"prod", "production", "grayscale"})
TERMINAL_PIPELINE_STATUSES = frozenset({"success", "failed", "canceled", "skipped"})
ACTION_REQUIRED_PIPELINE_STATUSES = frozenset({"manual", "blocked"})
ACTIVE_JOB_STATUSES = frozenset(
    {"created", "waiting_for_resource", "preparing", "pending", "running", "scheduled"}
)
VARIABLE_KEYS = ("ENVIRONMENT_NAME", "SUB_MODULE")
VARIABLE_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
SCP_REMOTE_RE = re.compile(r"^(?:[^@/]+@)?(?P<host>[^:/]+):(?P<path>.+)$")


class PipelineError(RuntimeError):
    """A user-facing GitLab configuration or API error."""


class PipelineRequestError(PipelineError):
    """A GitLab request failed and may or may not be retryable."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


class IgnoreUnknownTagLoader(yaml.SafeLoader):
    """Load GitLab CI YAML, including custom tags such as !reference."""


def _construct_unknown_tag(loader: yaml.SafeLoader, tag_suffix: str, node: yaml.Node) -> Any:
    del tag_suffix
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return None


IgnoreUnknownTagLoader.add_multi_constructor("!", _construct_unknown_tag)


def validate_base_url(value: str) -> str:
    try:
        return normalize_http_base_url(value, "GitLab base_url")
    except RuntimeConfigError as exc:
        raise PipelineError(str(exc)) from exc


def load_gitlab_runtime() -> dict[str, Any]:
    try:
        runtime = load_runtime(__file__)
    except RuntimeConfigError as exc:
        raise PipelineError(str(exc)) from exc
    gitlab_dir = runtime.get("gitlab_dir")
    if not gitlab_dir:
        raise PipelineError(
            "运行时配置缺少 gitlab_dir。请运行 common/bootstrap_work.py 初始化 GitLab 配置目录"
        )
    return runtime


def credentials_file(runtime: dict[str, Any] | None = None) -> Path:
    active_runtime = runtime or load_gitlab_runtime()
    return Path(str(active_runtime["gitlab_dir"])) / "gitlab-credentials.json"


def load_credentials() -> dict[str, str]:
    path = credentials_file()
    if not path.is_file():
        raise PipelineError(
            f"找不到 GitLab 凭证文件: {path}。"
            "请按 Skill 文档配置 base_url 和 private_token"
        )
    try:
        secure_private_directory(path.parent)
        secure_private_file(path)
    except PrivatePathError as exc:
        raise PipelineError(f"无法保护 GitLab 凭证文件: {exc}") from exc
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PipelineError(f"无法读取 GitLab 凭证文件: {path} ({exc})") from exc
    if not isinstance(payload, dict):
        raise PipelineError(f"GitLab 凭证文件顶层必须是 JSON object: {path}")
    base_url = str(payload.get("base_url") or "").strip()
    token = str(payload.get("private_token") or "").strip()
    if not base_url:
        raise PipelineError(f"GitLab 凭证文件缺少 base_url: {path}")
    if not token:
        raise PipelineError(f"GitLab 凭证文件缺少 private_token: {path}")
    return {
        "base_url": validate_base_url(base_url),
        "private_token": token,
    }


def normalize_project(project: str) -> str:
    value = project.strip().strip("/")
    parsed = parse_git_remote(value)
    if parsed is not None:
        return parsed["project"]
    if value.endswith(".git"):
        value = value[:-4]
    if not value or "/" not in value:
        raise PipelineError("项目路径必须是 namespace/project")
    if any(character in value for character in {"?", "#"}):
        raise PipelineError(f"项目路径包含无效字符: {project}")
    return value


def encode_project(project: str) -> str:
    return urllib_parse.quote(normalize_project(project), safe="")


def parse_git_remote(url: str) -> dict[str, str] | None:
    raw = url.strip()
    if not raw:
        return None
    host = ""
    path = ""
    if "://" in raw:
        parsed = urllib_parse.urlsplit(raw)
        if parsed.scheme not in {"http", "https", "ssh", "git"} or not parsed.hostname:
            return None
        host = parsed.hostname
        path = parsed.path
    else:
        match = SCP_REMOTE_RE.match(raw)
        if not match:
            return None
        host = match.group("host")
        path = match.group("path")
    path = urllib_parse.unquote(path.strip().strip("/"))
    if path.endswith(".git"):
        path = path[:-4]
    if not path or "/" not in path:
        return None
    return {"host": host, "project": path}


def configured_gitlab_host(credentials: dict[str, str]) -> str:
    host = urllib_parse.urlsplit(credentials["base_url"]).hostname
    if not host:
        raise PipelineError("GitLab base_url 无法解析 hostname")
    return host.casefold()


def validate_remote_host(host: str, credentials: dict[str, str]) -> None:
    expected = configured_gitlab_host(credentials)
    if host.casefold() != expected:
        raise PipelineError(
            f"Git remote host 与 GitLab 配置不一致: remote={host}, configured={expected}。"
            "请切换到正确仓库，或显式传 --project namespace/project"
        )


def run_git(cwd: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
        )
    except OSError as exc:
        raise PipelineError(f"无法执行 git: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip() or f"exit {result.returncode}"
        raise PipelineError(f"git {' '.join(args)} 失败: {detail}")
    return result.stdout.strip()


def resolve_from_git(cwd: Path | None = None) -> dict[str, Any]:
    starting_path = (cwd or Path.cwd()).expanduser().resolve()
    try:
        root = Path(run_git(starting_path, "rev-parse", "--show-toplevel"))
    except PipelineError as exc:
        raise PipelineError(f"当前目录不是 Git 仓库: {starting_path}") from exc
    remote_url = run_git(root, "remote", "get-url", "origin")
    parsed = parse_git_remote(remote_url)
    if parsed is None:
        raise PipelineError(f"无法从 origin 解析 GitLab 项目: {remote_url}")
    try:
        branch = run_git(root, "rev-parse", "--abbrev-ref", "HEAD")
    except PipelineError:
        branch = ""
    if branch == "HEAD":
        branch = ""
    return {
        "cwd": str(root),
        "remote_url": remote_url,
        "host": parsed["host"],
        "project": parsed["project"],
        "current_branch": branch or None,
    }


def _decode_error_body(body: bytes) -> str:
    if not body:
        return ""
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return body.decode("utf-8", errors="replace")[:300]
    if isinstance(payload, dict):
        message = payload.get("message") or payload.get("error")
        if isinstance(message, dict):
            return json.dumps(message, ensure_ascii=False)
        if message:
            return str(message)
    return json.dumps(payload, ensure_ascii=False)[:300]


def api_request(
    credentials: dict[str, str],
    method: str,
    path: str,
    *,
    query: Sequence[tuple[str, str]] | None = None,
    payload: dict[str, Any] | None = None,
    raw: bool = False,
    timeout: int = DEFAULT_REQUEST_TIMEOUT_SECONDS,
) -> Any:
    url = f"{credentials['base_url']}/api/v4{path}"
    if query:
        url = f"{url}?{urllib_parse.urlencode(query, doseq=True)}"
    headers = {
        "PRIVATE-TOKEN": credentials["private_token"],
        "Accept": "application/json",
        "User-Agent": "agent-toolkit-gitlab-run-pipeline/2",
    }
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib_request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib_request.urlopen(request, timeout=timeout) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
            status = getattr(response, "status", 200)
    except urllib_error.HTTPError as exc:
        error_body = exc.read(MAX_RESPONSE_BYTES) if exc.fp else b""
        detail = _decode_error_body(error_body) or str(exc.reason)
        raise PipelineRequestError(
            f"GitLab HTTP {exc.code}: {detail}",
            status_code=exc.code,
            retryable=exc.code == 429 or exc.code >= 500,
        ) from exc
    except urllib_error.URLError as exc:
        raise PipelineRequestError(
            f"无法连接 GitLab: {exc.reason}",
            retryable=True,
        ) from exc
    except TimeoutError as exc:
        raise PipelineRequestError(
            f"GitLab 请求超过 {timeout} 秒",
            retryable=True,
        ) from exc
    if len(body) > MAX_RESPONSE_BYTES:
        raise PipelineRequestError("GitLab 响应超过 2 MiB 限制")
    if status >= 400:
        raise PipelineRequestError(
            f"GitLab HTTP {status}",
            status_code=status,
            retryable=status == 429 or status >= 500,
        )
    if raw:
        try:
            return body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PipelineRequestError("GitLab 返回的 CI 配置不是有效 UTF-8") from exc
    if not body:
        return {}
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PipelineRequestError("GitLab 返回的不是有效 UTF-8 JSON") from exc


def project_api_path(project: str) -> str:
    return f"/projects/{encode_project(project)}"


def summarize_project(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": payload.get("id"),
        "name": payload.get("name"),
        "path": payload.get("path"),
        "path_with_namespace": payload.get("path_with_namespace"),
        "web_url": payload.get("web_url"),
        "default_branch": payload.get("default_branch"),
        "visibility": payload.get("visibility"),
        "last_activity_at": payload.get("last_activity_at"),
    }


def search_projects(
    credentials: dict[str, str],
    query_text: str,
) -> dict[str, Any]:
    query_text = query_text.strip()
    if not 2 <= len(query_text) <= 100:
        raise PipelineError("项目搜索关键词长度必须在 2-100 个字符之间")
    payload = api_request(
        credentials,
        "GET",
        "/projects",
        query=[
            ("membership", "true"),
            ("simple", "true"),
            ("search", query_text),
            ("per_page", str(PROJECT_SEARCH_LIMIT)),
            ("order_by", "last_activity_at"),
            ("sort", "desc"),
        ],
    )
    if not isinstance(payload, list):
        raise PipelineError("GitLab 项目搜索返回格式无效")
    candidates = [summarize_project(item) for item in payload if isinstance(item, dict)]
    needle = query_text.casefold()
    exact_matches = [
        project
        for project in candidates
        if needle
        in {
            str(project.get("name") or "").casefold(),
            str(project.get("path") or "").casefold(),
            str(project.get("path_with_namespace") or "").casefold(),
        }
    ]
    return {
        "query": query_text,
        "candidates": candidates,
        "exact_matches": exact_matches,
        "selection_required": len(candidates) != 1 or len(exact_matches) != 1,
        "truncated": len(candidates) >= PROJECT_SEARCH_LIMIT,
    }


def load_ci_yaml(text: str) -> dict[str, Any]:
    try:
        payload = yaml.load(text, Loader=IgnoreUnknownTagLoader)
    except yaml.YAMLError as exc:
        raise PipelineError(f"无法解析 GitLab CI 配置: {exc}") from exc
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise PipelineError("GitLab CI 配置顶层必须是 mapping")
    return payload


def extract_variable_spec(ci: dict[str, Any], key: str) -> dict[str, Any] | None:
    variables = ci.get("variables")
    if not isinstance(variables, dict):
        return None
    raw = variables.get(key)
    if raw is None:
        return None
    if isinstance(raw, dict):
        options = raw.get("options")
        return {
            "key": key,
            "default": None if raw.get("value") is None else str(raw.get("value")),
            "options": [str(item) for item in options] if isinstance(options, list) else [],
            "description": str(raw.get("description") or "") or None,
        }
    return {"key": key, "default": str(raw), "options": [], "description": None}


def fetch_ci_text(credentials: dict[str, str], project: str, ref: str) -> str:
    return str(
        api_request(
            credentials,
            "GET",
            f"{project_api_path(project)}/repository/files/{urllib_parse.quote('.gitlab-ci.yml', safe='')}/raw",
            query=[("ref", ref)],
            raw=True,
        )
    )


def lint_requires_content(payload: dict[str, Any]) -> bool:
    errors = payload.get("errors")
    if payload.get("valid") is not False or not isinstance(errors, list):
        return False
    return any(
        "please provide content of .gitlab-ci.yml" in str(error).casefold()
        for error in errors
    )


def lint_ci_config(credentials: dict[str, str], project: str, ref: str) -> dict[str, Any]:
    path = f"{project_api_path(project)}/ci/lint"
    queries = (
        [("content_ref", ref), ("include_jobs", "true")],
        [("sha", ref), ("include_jobs", "true")],
    )
    last_error: PipelineRequestError | None = None
    compatibility_errors: list[str] = []
    for query in queries:
        try:
            payload = api_request(credentials, "GET", path, query=query)
        except PipelineRequestError as exc:
            if exc.status_code not in {400, 404}:
                raise
            last_error = exc
            continue
        if not isinstance(payload, dict):
            raise PipelineError("GitLab CI lint 返回格式无效")
        valid = payload.get("valid")
        if not isinstance(valid, bool):
            raise PipelineError("GitLab CI lint 返回缺少 valid 字段")
        if lint_requires_content(payload):
            compatibility_errors.extend(str(item) for item in payload.get("errors", []))
            continue
        return {
            "available": True,
            "valid": valid,
            "errors": payload.get("errors") if isinstance(payload.get("errors"), list) else [],
            "warnings": payload.get("warnings") if isinstance(payload.get("warnings"), list) else [],
            "jobs_count": len(payload.get("jobs")) if isinstance(payload.get("jobs"), list) else None,
            "merged_yaml": payload.get("merged_yaml") if isinstance(payload.get("merged_yaml"), str) else None,
        }
    return {
        "available": False,
        "valid": None,
        "errors": [],
        "warnings": [
            *([str(last_error)] if last_error else []),
            *dict.fromkeys(compatibility_errors),
        ],
        "jobs_count": None,
        "merged_yaml": None,
    }


def fetch_ref_commit(
    credentials: dict[str, str],
    project: str,
    ref: str,
) -> dict[str, Any]:
    payload = api_request(
        credentials,
        "GET",
        f"{project_api_path(project)}/repository/commits/{urllib_parse.quote(ref, safe='')}",
    )
    if not isinstance(payload, dict) or not payload.get("id"):
        raise PipelineError(f"GitLab ref 无法解析到 commit: {ref}")
    return {
        "id": str(payload["id"]),
        "short_id": payload.get("short_id"),
        "title": payload.get("title"),
        "committed_date": payload.get("committed_date"),
        "web_url": payload.get("web_url"),
    }


def is_prod_environment(value: str | None) -> bool:
    return bool(value) and value.strip().lower() in PROD_ENVIRONMENTS


def build_pipeline_variables(
    *,
    environment: str | None,
    module: str | None,
    extra: Sequence[str] | None = None,
) -> list[dict[str, str]]:
    variables: list[dict[str, str]] = []
    seen: set[str] = set()

    def add_variable(key: str, value: str) -> None:
        if key in seen:
            raise PipelineError(f"Pipeline 变量重复: {key}")
        seen.add(key)
        variables.append({"key": key, "value": value})

    if environment:
        add_variable("ENVIRONMENT_NAME", environment)
    if module:
        add_variable("SUB_MODULE", module)
    for item in extra or []:
        if "=" not in item:
            raise PipelineError(f"额外变量必须是 KEY=VALUE: {item}")
        key, value = item.split("=", 1)
        key = key.strip()
        if not VARIABLE_NAME_RE.fullmatch(key):
            raise PipelineError(f"额外变量名无效: {key}")
        if key in {"ENVIRONMENT_NAME", "SUB_MODULE"}:
            raise PipelineError(f"{key} 请使用 --env / --module，不要用 --var")
        if key.startswith("CI_"):
            raise PipelineError(f"禁止覆盖 GitLab 预定义变量: {key}")
        add_variable(key, value)
    return variables


def validate_trigger_plan(
    *,
    environment: str | None,
    module: str | None,
    ci_variables: Sequence[dict[str, Any]],
    lint: dict[str, Any],
    allow_prod: bool,
    confirm: bool,
) -> None:
    specs = {item["key"]: item for item in ci_variables}
    env_spec = specs.get("ENVIRONMENT_NAME")
    module_spec = specs.get("SUB_MODULE")
    if environment and env_spec and env_spec.get("options") and environment not in env_spec["options"]:
        raise PipelineError(
            f"ENVIRONMENT_NAME={environment} 不在可选值中: {', '.join(env_spec['options'])}"
        )
    if module and module_spec and module_spec.get("options") and module not in module_spec["options"]:
        raise PipelineError(
            f"SUB_MODULE={module} 不在可选值中: {', '.join(module_spec['options'])}"
        )
    if is_prod_environment(environment) and not allow_prod:
        raise PipelineError(
            f"ENVIRONMENT_NAME={environment} 属于生产/灰度，必须由用户明确授权后加 --allow-prod"
        )
    if lint.get("available") and lint.get("valid") is False:
        errors = lint.get("errors") or ["unknown CI lint error"]
        raise PipelineError(f"GitLab CI 配置校验失败: {'; '.join(str(item) for item in errors)}")
    if not confirm:
        raise PipelineError("触发 pipeline 需要 --confirm；先 inspect 并把计划给用户确认")


def resolve_trigger_inputs(
    inspected: dict[str, Any],
    args: argparse.Namespace,
) -> tuple[str | None, str | None, list[dict[str, str]]]:
    environment_selected = getattr(args, "env", None) is not None or bool(
        getattr(args, "no_environment", False)
    )
    if not environment_selected:
        raise PipelineError("准备触发计划时必须显式传 --env 或 --no-environment")
    environment = None if getattr(args, "no_environment", False) else args.env
    if environment is not None:
        environment = environment.strip()
        if not environment:
            raise PipelineError("--env 不能为空")
    module_spec = next(
        (item for item in inspected["ci"]["variables"] if item["key"] == "SUB_MODULE"),
        None,
    )
    requested_module = getattr(args, "module", None)
    module = requested_module if requested_module is not None else (
        None if module_spec is None else module_spec.get("default")
    )
    variables = build_pipeline_variables(
        environment=environment,
        module=module,
        extra=getattr(args, "var", []),
    )
    return environment, module, variables


def build_trigger_plan(
    inspected: dict[str, Any],
    *,
    environment: str | None,
    module: str | None,
    variables: Sequence[dict[str, str]],
) -> dict[str, Any]:
    project_id = inspected.get("project_id")
    project = inspected.get("project")
    ref = inspected.get("ref")
    commit = inspected.get("commit")
    ci_config_sha256 = inspected.get("ci", {}).get("config_sha256")
    if not isinstance(project_id, int) or not isinstance(project, str):
        raise PipelineError("无法从 inspect 结果生成项目确认计划")
    if not isinstance(ref, str) or not isinstance(commit, dict) or not commit.get("id"):
        raise PipelineError("无法从 inspect 结果生成 ref 确认计划")
    if not isinstance(ci_config_sha256, str):
        raise PipelineError("无法从 inspect 结果生成 CI 配置确认计划")
    payload = {
        "version": TRIGGER_PLAN_VERSION,
        "project_id": project_id,
        "project": project,
        "ref": ref,
        "commit_sha": str(commit["id"]),
        "ci_config_sha256": ci_config_sha256,
        "environment": environment,
        "module": module,
        "variables": sorted(
            ({"key": item["key"], "value": item["value"]} for item in variables),
            key=lambda item: item["key"],
        ),
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        **payload,
        "plan_id": f"sha256:{hashlib.sha256(serialized.encode('utf-8')).hexdigest()}",
    }


def validate_plan_id(expected: str, provided: str) -> None:
    if provided != expected:
        raise PipelineError(
            "触发计划已变化或 --plan-id 不匹配。请重新运行 inspect，展示最新项目、commit 和参数后再次确认"
        )


def summarize_user(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    return {
        "id": payload.get("id"),
        "username": payload.get("username"),
        "name": payload.get("name"),
        "web_url": payload.get("web_url"),
    }


def summarize_pipeline(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": payload.get("id"),
        "iid": payload.get("iid"),
        "status": payload.get("status"),
        "source": payload.get("source"),
        "ref": payload.get("ref"),
        "sha": payload.get("sha"),
        "web_url": payload.get("web_url"),
        "created_at": payload.get("created_at"),
        "updated_at": payload.get("updated_at"),
        "started_at": payload.get("started_at"),
        "finished_at": payload.get("finished_at"),
        "duration": payload.get("duration"),
        "queued_duration": payload.get("queued_duration"),
        "user": summarize_user(payload.get("user")),
    }


def summarize_job(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": payload.get("id"),
        "name": payload.get("name"),
        "stage": payload.get("stage"),
        "status": payload.get("status"),
        "allow_failure": bool(payload.get("allow_failure")),
        "failure_reason": payload.get("failure_reason"),
        "created_at": payload.get("created_at"),
        "started_at": payload.get("started_at"),
        "finished_at": payload.get("finished_at"),
        "duration": payload.get("duration"),
        "queued_duration": payload.get("queued_duration"),
        "web_url": payload.get("web_url"),
    }


def fetch_pipeline_jobs(
    credentials: dict[str, str],
    project: str,
    pipeline_id: int,
) -> tuple[list[dict[str, Any]], bool]:
    jobs: list[dict[str, Any]] = []
    truncated = False
    for page in range(1, MAX_JOB_PAGES + 1):
        payload = api_request(
            credentials,
            "GET",
            f"{project_api_path(project)}/pipelines/{pipeline_id}/jobs",
            query=[
                ("per_page", str(JOBS_PER_PAGE)),
                ("page", str(page)),
                ("include_retried", "false"),
            ],
        )
        if not isinstance(payload, list):
            raise PipelineError("GitLab jobs 返回格式无效")
        jobs.extend(summarize_job(item) for item in payload if isinstance(item, dict))
        if len(payload) < JOBS_PER_PAGE:
            break
        if page == MAX_JOB_PAGES:
            truncated = True
    return jobs, truncated


def fetch_pipeline_parameters(
    credentials: dict[str, str],
    project: str,
    pipeline_id: int,
) -> dict[str, Any]:
    try:
        payload = api_request(
            credentials,
            "GET",
            f"{project_api_path(project)}/pipelines/{pipeline_id}/variables",
        )
    except PipelineRequestError as exc:
        return {
            "available": False,
            "environment": None,
            "module": None,
            "reason": f"HTTP {exc.status_code}" if exc.status_code else "request failed",
        }
    if not isinstance(payload, list):
        return {
            "available": False,
            "environment": None,
            "module": None,
            "reason": "invalid response",
        }
    selected: dict[str, str] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "")
        if key in VARIABLE_KEYS:
            selected[key] = str(item.get("value") or "")
    return {
        "available": True,
        "environment": selected.get("ENVIRONMENT_NAME"),
        "module": selected.get("SUB_MODULE"),
    }


def build_pipeline_result(
    pipeline: dict[str, Any],
    jobs: Sequence[dict[str, Any]],
    *,
    jobs_truncated: bool = False,
    timed_out: bool = False,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    status = str(pipeline.get("status") or "unknown")
    counts = Counter(str(job.get("status") or "unknown") for job in jobs)
    failed_jobs = [
        job for job in jobs if job.get("status") == "failed" and not job.get("allow_failure")
    ]
    allowed_failures = [
        job for job in jobs if job.get("status") == "failed" and job.get("allow_failure")
    ]
    active_jobs = [job for job in jobs if job.get("status") in ACTIVE_JOB_STATUSES]
    manual_jobs = [job for job in jobs if job.get("status") == "manual"]
    if timed_out:
        outcome = "timeout"
    elif status == "success":
        outcome = "success"
    elif status in TERMINAL_PIPELINE_STATUSES:
        outcome = "failed"
    elif status in ACTION_REQUIRED_PIPELINE_STATUSES:
        outcome = "requires_action"
    else:
        outcome = "in_progress"
    return {
        "outcome": outcome,
        "pipeline": pipeline,
        "parameters": parameters,
        "job_summary": {
            "total": len(jobs),
            "counts": dict(sorted(counts.items())),
            "truncated": jobs_truncated,
        },
        "active_jobs": active_jobs,
        "failed_jobs": failed_jobs,
        "allowed_failures": allowed_failures,
        "manual_jobs": manual_jobs,
    }


def get_pipeline_result(
    credentials: dict[str, str],
    project: str,
    pipeline_id: int,
    *,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = api_request(
        credentials,
        "GET",
        f"{project_api_path(project)}/pipelines/{pipeline_id}",
    )
    if not isinstance(payload, dict):
        raise PipelineError("GitLab pipeline 状态格式无效")
    jobs, truncated = fetch_pipeline_jobs(credentials, project, pipeline_id)
    resolved_parameters = parameters or fetch_pipeline_parameters(
        credentials,
        project,
        pipeline_id,
    )
    return build_pipeline_result(
        summarize_pipeline(payload),
        jobs,
        jobs_truncated=truncated,
        parameters=resolved_parameters,
    )


def progress_signature(result: dict[str, Any]) -> tuple[Any, ...]:
    pipeline = result["pipeline"]
    counts = result["job_summary"]["counts"]
    active = tuple(
        sorted((job.get("id"), job.get("status"), job.get("name")) for job in result["active_jobs"])
    )
    return pipeline.get("status"), tuple(sorted(counts.items())), active


def emit_monitor_progress(result: dict[str, Any]) -> None:
    pipeline = result["pipeline"]
    counts = result["job_summary"]["counts"]
    counts_text = ", ".join(f"{key}={value}" for key, value in sorted(counts.items())) or "no-jobs"
    active_names = ", ".join(str(job.get("name")) for job in result["active_jobs"][:5])
    active_text = f" active=[{active_names}]" if active_names else ""
    print(
        f"[monitor] pipeline={pipeline.get('id')} status={pipeline.get('status')} {counts_text}{active_text}",
        file=sys.stderr,
        flush=True,
    )


def validate_monitor_options(interval: int, timeout: int) -> None:
    if not MIN_MONITOR_INTERVAL_SECONDS <= interval <= MAX_MONITOR_INTERVAL_SECONDS:
        raise PipelineError(
            f"--interval 必须在 {MIN_MONITOR_INTERVAL_SECONDS}-{MAX_MONITOR_INTERVAL_SECONDS} 秒之间"
        )
    if not interval <= timeout <= MAX_MONITOR_TIMEOUT_SECONDS:
        raise PipelineError(
            f"--timeout 必须在 interval 到 {MAX_MONITOR_TIMEOUT_SECONDS} 秒之间"
        )


def monitor_pipeline(
    credentials: dict[str, str],
    project: str,
    pipeline_id: int,
    *,
    interval: int = DEFAULT_MONITOR_INTERVAL_SECONDS,
    timeout: int = DEFAULT_MONITOR_TIMEOUT_SECONDS,
    emit: Callable[[dict[str, Any]], None] | None = emit_monitor_progress,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    validate_monitor_options(interval, timeout)
    parameters = fetch_pipeline_parameters(credentials, project, pipeline_id)
    started_at = clock()
    last_emit_at = started_at - MONITOR_HEARTBEAT_SECONDS
    last_signature: tuple[Any, ...] | None = None
    consecutive_errors = 0
    while True:
        try:
            result = get_pipeline_result(
                credentials,
                project,
                pipeline_id,
                parameters=parameters,
            )
        except PipelineRequestError as exc:
            if not exc.retryable:
                raise
            consecutive_errors += 1
            if consecutive_errors >= MAX_CONSECUTIVE_MONITOR_ERRORS:
                raise PipelineError(
                    f"连续 {consecutive_errors} 次无法读取 pipeline 状态，停止监控: {exc}"
                ) from exc
            print(
                f"[monitor] 临时请求失败，准备重试 ({consecutive_errors}/{MAX_CONSECUTIVE_MONITOR_ERRORS}): {exc}",
                file=sys.stderr,
                flush=True,
            )
            sleeper(interval)
            continue
        consecutive_errors = 0
        now = clock()
        signature = progress_signature(result)
        if emit is not None and (
            signature != last_signature or now - last_emit_at >= MONITOR_HEARTBEAT_SECONDS
        ):
            emit(result)
            last_emit_at = now
            last_signature = signature
        if result["outcome"] in {"success", "failed", "requires_action"}:
            return result
        if now - started_at >= timeout:
            timed_out_result = dict(result)
            timed_out_result["outcome"] = "timeout"
            timed_out_result["timed_out"] = True
            return timed_out_result
        sleeper(interval)


def _resolved_project(
    args: argparse.Namespace,
    credentials: dict[str, str],
) -> tuple[str, dict[str, Any] | None]:
    if getattr(args, "project", None):
        raw_project = str(args.project)
        parsed = parse_git_remote(raw_project)
        if parsed is not None:
            validate_remote_host(parsed["host"], credentials)
        return normalize_project(raw_project), None
    cwd = Path(args.cwd).expanduser() if getattr(args, "cwd", None) else Path.cwd()
    git_info = resolve_from_git(cwd)
    validate_remote_host(str(git_info["host"]), credentials)
    return str(git_info["project"]), git_info


def inspect_project(
    credentials: dict[str, str],
    project: str,
    ref: str,
) -> dict[str, Any]:
    project_info = api_request(credentials, "GET", project_api_path(project))
    if not isinstance(project_info, dict):
        raise PipelineError("GitLab 项目信息格式无效")
    project_id = project_info.get("id")
    canonical_project = project_info.get("path_with_namespace")
    if not isinstance(project_id, int) or not isinstance(canonical_project, str):
        raise PipelineError("GitLab 项目信息缺少 id 或 path_with_namespace")
    if canonical_project.casefold() != normalize_project(project).casefold():
        raise PipelineError(
            f"GitLab 返回的规范项目路径与请求不一致: requested={project}, actual={canonical_project}"
        )
    lint = lint_ci_config(credentials, project, ref)
    ci_text = lint.get("merged_yaml") or fetch_ci_text(credentials, project, ref)
    ci = load_ci_yaml(str(ci_text))
    commit = fetch_ref_commit(credentials, canonical_project, ref)
    variables = [spec for key in VARIABLE_KEYS if (spec := extract_variable_spec(ci, key))]
    env_spec = next((item for item in variables if item["key"] == "ENVIRONMENT_NAME"), None)
    module_spec = next((item for item in variables if item["key"] == "SUB_MODULE"), None)
    return {
        "base_url": credentials["base_url"],
        "project": canonical_project,
        "project_id": project_id,
        "web_url": project_info.get("web_url"),
        "default_branch": project_info.get("default_branch"),
        "ref": ref,
        "commit": commit,
        "ci": {
            "source": "gitlab-lint-merged" if lint.get("merged_yaml") else "remote-ref-root",
            "config_sha256": hashlib.sha256(str(ci_text).encode("utf-8")).hexdigest(),
            "lint": {key: value for key, value in lint.items() if key != "merged_yaml"},
            "variables": variables,
            "pipeline_source": "api; GitLab Create Pipeline API is authoritative",
        },
        "suggested": {
            "ref": ref,
            "environment": None if env_spec is None else env_spec.get("default"),
            "module": None if module_spec is None else module_spec.get("default"),
        },
    }


def cmd_whoami(_args: argparse.Namespace) -> dict[str, Any]:
    credentials = load_credentials()
    user = api_request(credentials, "GET", "/user")
    if not isinstance(user, dict):
        raise PipelineError("GitLab /user 返回格式无效")
    return {
        "base_url": credentials["base_url"],
        "credentials_file": str(credentials_file()),
        "user": summarize_user(user),
    }


def cmd_search(args: argparse.Namespace) -> dict[str, Any]:
    credentials = load_credentials()
    return {
        "base_url": credentials["base_url"],
        **search_projects(credentials, args.query),
    }


def cmd_resolve(args: argparse.Namespace) -> dict[str, Any]:
    credentials = load_credentials()
    cwd = Path(args.cwd).expanduser() if args.cwd else Path.cwd()
    resolved = resolve_from_git(cwd)
    validate_remote_host(str(resolved["host"]), credentials)
    resolved["base_url"] = credentials["base_url"]
    return resolved


def cmd_inspect(args: argparse.Namespace) -> dict[str, Any]:
    credentials = load_credentials()
    project, git_info = _resolved_project(args, credentials)
    ref = args.ref or (git_info or {}).get("current_branch")
    if not ref:
        project_info = api_request(credentials, "GET", project_api_path(project))
        if not isinstance(project_info, dict):
            raise PipelineError("GitLab 项目信息格式无效")
        ref = project_info.get("default_branch")
    if not ref:
        raise PipelineError("无法确定 ref，请显式传入 --ref")
    inspected = inspect_project(credentials, project, str(ref))
    plan_arguments_present = (
        args.env is not None
        or args.no_environment
        or args.module is not None
        or bool(args.var)
    )
    if not plan_arguments_present:
        inspected["trigger_plan"] = None
        return inspected
    environment, module, variables = resolve_trigger_inputs(inspected, args)
    validate_trigger_plan(
        environment=environment,
        module=module,
        ci_variables=inspected["ci"]["variables"],
        lint=inspected["ci"]["lint"],
        allow_prod=True,
        confirm=True,
    )
    inspected["trigger_plan"] = build_trigger_plan(
        inspected,
        environment=environment,
        module=module,
        variables=variables,
    )
    return inspected


def cmd_trigger(args: argparse.Namespace) -> dict[str, Any]:
    credentials = load_credentials()
    project, _git_info = _resolved_project(args, credentials)
    inspected = inspect_project(credentials, project, args.ref)
    environment, module, variables = resolve_trigger_inputs(inspected, args)
    trigger_plan = build_trigger_plan(
        inspected,
        environment=environment,
        module=module,
        variables=variables,
    )
    validate_plan_id(trigger_plan["plan_id"], args.plan_id)
    validate_trigger_plan(
        environment=environment,
        module=module,
        ci_variables=inspected["ci"]["variables"],
        lint=inspected["ci"]["lint"],
        allow_prod=args.allow_prod,
        confirm=args.confirm,
    )
    created = api_request(
        credentials,
        "POST",
        f"{project_api_path(inspected['project'])}/pipeline",
        payload={"ref": inspected["ref"], "variables": variables},
    )
    if not isinstance(created, dict) or not isinstance(created.get("id"), int):
        raise PipelineError("GitLab 创建 pipeline 返回格式无效")
    triggered = {
        "project": inspected["project"],
        "ref": inspected["ref"],
        "environment": environment,
        "module": module,
        "variables": variables,
        "trigger_plan": trigger_plan,
        "pipeline": summarize_pipeline(created),
    }
    if args.no_wait:
        return {"triggered": triggered, "monitoring": False}
    result = monitor_pipeline(
        credentials,
        inspected["project"],
        int(created["id"]),
        interval=args.interval,
        timeout=args.timeout,
    )
    return {"triggered": triggered, "monitoring": True, "result": result}


def latest_pipeline_id(
    credentials: dict[str, str],
    project: str,
    ref: str | None,
) -> int:
    query: list[tuple[str, str]] = [
        ("per_page", "1"),
        ("order_by", "id"),
        ("sort", "desc"),
    ]
    if ref:
        query.append(("ref", ref))
    payload = api_request(
        credentials,
        "GET",
        f"{project_api_path(project)}/pipelines",
        query=query,
    )
    if not isinstance(payload, list):
        raise PipelineError("GitLab pipelines 返回格式无效")
    if not payload or not isinstance(payload[0], dict) or not isinstance(payload[0].get("id"), int):
        suffix = f" ref={ref}" if ref else ""
        raise PipelineError(f"没有找到可查询的 pipeline:{suffix}")
    return int(payload[0]["id"])


def cmd_result(args: argparse.Namespace) -> dict[str, Any]:
    credentials = load_credentials()
    project, git_info = _resolved_project(args, credentials)
    ref = args.ref or (git_info or {}).get("current_branch")
    if args.pipeline_id is None and not ref:
        raise PipelineError("未传 --pipeline-id 时必须能确定 ref；请显式传 --ref")
    pipeline_id = args.pipeline_id or latest_pipeline_id(credentials, project, ref)
    if args.wait:
        result = monitor_pipeline(
            credentials,
            project,
            pipeline_id,
            interval=args.interval,
            timeout=args.timeout,
        )
    else:
        result = get_pipeline_result(credentials, project, pipeline_id)
    return {
        "project": project,
        "selected_by": "pipeline-id" if args.pipeline_id else "latest-ref",
        "requested_ref": ref,
        "result": result,
    }


def add_project_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project")
    parser.add_argument("--cwd")


def add_monitor_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--interval", type=int, default=DEFAULT_MONITOR_INTERVAL_SECONDS)
    parser.add_argument("--timeout", type=int, default=DEFAULT_MONITOR_TIMEOUT_SECONDS)


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("必须是正整数")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="通过 GitLab API 触发 Pipeline，并查询或等待最终结果",
        allow_abbrev=False,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("whoami", help="验证本机 GitLab 配置和 Token")

    search = subparsers.add_parser("search", help="按模糊名称只读搜索有成员关系的项目")
    search.add_argument("--query", required=True)

    resolve = subparsers.add_parser("resolve", help="从 git origin 解析 GitLab 项目")
    resolve.add_argument("--cwd")

    inspect = subparsers.add_parser("inspect", help="检查准确 ref 的项目和 CI 参数")
    add_project_arguments(inspect)
    inspect.add_argument("--ref")
    inspect_environment = inspect.add_mutually_exclusive_group()
    inspect_environment.add_argument("--env", help="为触发计划指定 ENVIRONMENT_NAME")
    inspect_environment.add_argument(
        "--no-environment",
        action="store_true",
        help="该 pipeline 明确没有 ENVIRONMENT_NAME",
    )
    inspect.add_argument("--module", help="为触发计划指定 SUB_MODULE")
    inspect.add_argument("--var", action="append", default=[], help="触发计划中的非敏感 KEY=VALUE")

    trigger = subparsers.add_parser("trigger", help="创建 pipeline，默认等待最终结果")
    add_project_arguments(trigger)
    trigger.add_argument("--ref", required=True)
    environment = trigger.add_mutually_exclusive_group(required=True)
    environment.add_argument("--env", help="ENVIRONMENT_NAME，例如 test")
    environment.add_argument(
        "--no-environment",
        action="store_true",
        help="该 pipeline 明确没有 ENVIRONMENT_NAME",
    )
    trigger.add_argument("--module", help="SUB_MODULE，例如 all")
    trigger.add_argument("--var", action="append", default=[], help="非敏感额外 KEY=VALUE，可重复")
    trigger.add_argument("--confirm", action="store_true", help="用户确认后再触发")
    trigger.add_argument("--plan-id", required=True, help="inspect 返回且已由用户确认的计划 ID")
    trigger.add_argument("--allow-prod", action="store_true", help="允许 prod/production/grayscale")
    trigger.add_argument("--no-wait", action="store_true", help="只触发，不等待最终结果")
    add_monitor_arguments(trigger)

    result = subparsers.add_parser("result", help="按 ID 或 ref 最新一次查询执行结果")
    add_project_arguments(result)
    selector = result.add_mutually_exclusive_group()
    selector.add_argument("--pipeline-id", type=positive_int)
    selector.add_argument("--ref")
    result.add_argument("--wait", action="store_true", help="尚未结束时等待最终结果")
    add_monitor_arguments(result)

    return parser


COMMANDS = {
    "whoami": cmd_whoami,
    "search": cmd_search,
    "resolve": cmd_resolve,
    "inspect": cmd_inspect,
    "trigger": cmd_trigger,
    "result": cmd_result,
}


def result_exit_code(result: dict[str, Any]) -> int:
    candidate = result.get("result")
    if isinstance(candidate, dict) and "outcome" not in candidate:
        nested = candidate.get("result")
        if isinstance(nested, dict):
            candidate = nested
    if not isinstance(candidate, dict):
        return 0
    outcome = candidate.get("outcome")
    if outcome == "failed":
        return 1
    if outcome in {"timeout", "requires_action"}:
        return 3
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = COMMANDS[args.command](args)
    except PipelineError as exc:
        print(f"[!] {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result_exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())
