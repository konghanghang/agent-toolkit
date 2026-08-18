#!/usr/bin/env python3
"""Safely query, compare, verify, and deliberately mutate test MySQL data."""

from __future__ import annotations

import argparse
import base64
import contextlib
import datetime as dt
import decimal
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


COMMON_DIR = Path(__file__).resolve().parents[3] / "common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from private_files import (
    PrivatePathError,
    atomic_write_private_json,
    secure_private_directory,
    secure_private_file,
)
from runtime import load_runtime_or_exit


PROFILE_FILE_NAME = "profiles.json"
ROUTE_CACHE_FILE_NAME = "database-routes.json"
SYSTEM_DATABASES = frozenset({"information_schema", "mysql", "performance_schema", "sys"})
SAFE_READ_STATEMENTS = frozenset({"select"})
SAFE_WRITE_STATEMENTS = frozenset({"insert", "update", "delete"})
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_$-]+$")
ARTIFACT_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
SENSITIVE_COLUMN_RE = re.compile(
    r"(?:password|passwd|secret|token|cookie|authorization|mobile|phone|email|id_?card)",
    re.IGNORECASE,
)
MAX_QUERY_ROWS = 500
MAX_VERIFY_TIMEOUT = 60.0
MAX_WRITE_ROWS = 1000
ROUTE_CACHE_TTL_SECONDS = 24 * 60 * 60
PROFILE_PLACEHOLDERS = {
    "host": "测试库地址",
    "username": "测试库账号",
}


class TestDbError(RuntimeError):
    """Expected configuration, policy, or execution failure."""


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def utc_text(value: dt.datetime | None = None) -> str:
    return (value or utc_now()).isoformat().replace("+00:00", "Z")


def runtime_directory() -> Path:
    runtime = load_runtime_or_exit(__file__)
    return Path(runtime["test_db_dir"])


def ensure_private_directory(path: Path) -> None:
    try:
        secure_private_directory(path)
    except PrivatePathError as exc:
        raise TestDbError(f"无法保护测试库运行目录: {path} ({exc})") from exc


def ensure_private_file(path: Path) -> None:
    try:
        secure_private_file(path)
    except PrivatePathError as exc:
        raise TestDbError(f"无法保护测试库私有文件: {path} ({exc})") from exc


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    atomic_write_private_json(path, payload, default=json_default)


def json_default(value: Any) -> Any:
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, bytes):
        return {"base64": base64.b64encode(value).decode("ascii")}
    raise TypeError(f"无法序列化 {type(value).__name__}")


def jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=json_default))


def validate_identifier(value: str, label: str) -> str:
    if not IDENTIFIER_RE.fullmatch(value):
        raise TestDbError(f"{label} 只能包含字母、数字、_、$ 或 -: {value!r}")
    return value


def quote_identifier(value: str, label: str) -> str:
    return f"`{validate_identifier(value, label)}`"


def normalize_scope(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise TestDbError(f"{field} 必须是字符串数组")
    if "*" in value and value != ["*"]:
        raise TestDbError(f"{field} 使用 * 时不能混入具体数据库名")
    if len(set(value)) != len(value):
        raise TestDbError(f"{field} 不能包含重复项")
    for item in value:
        if item != "*":
            validate_identifier(item, field)
            if item.lower() in SYSTEM_DATABASES:
                raise TestDbError(f"{field} 不得包含系统库: {item}")
    return tuple(value)


def validate_profile(name: str, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise TestDbError(f"profile {name!r} 必须是 JSON 对象")
    profile = dict(raw)
    if profile.get("environment") != "test":
        raise TestDbError(f"profile {name!r} 必须显式配置 environment=test")
    if profile.get("engine", "mysql") != "mysql":
        raise TestDbError(f"profile {name!r} 当前只支持 engine=mysql")

    host = profile.get("host")
    username = profile.get("username")
    if not isinstance(host, str) or not host.strip():
        raise TestDbError(f"profile {name!r} 缺少 host")
    if not isinstance(username, str) or not username.strip():
        raise TestDbError(f"profile {name!r} 缺少 username")
    try:
        port = int(profile.get("port", 3306))
    except (TypeError, ValueError) as exc:
        raise TestDbError(f"profile {name!r} 的 port 必须是整数") from exc
    if not 1 <= port <= 65535:
        raise TestDbError(f"profile {name!r} 的 port 超出范围")

    allowed = normalize_scope(profile.get("allowed_databases"), "allowed_databases")
    writable = normalize_scope(profile.get("writable_databases", []), "writable_databases")
    if writable == ("*",) and allowed != ("*",):
        raise TestDbError("writable_databases=[\"*\"] 时 allowed_databases 也必须是 [\"*\"]")
    if allowed != ("*",) and any(database not in allowed for database in writable):
        raise TestDbError("writable_databases 必须是 allowed_databases 的子集")

    password = profile.get("password")
    password_env = profile.get("password_env")
    if password is not None and not isinstance(password, str):
        raise TestDbError(f"profile {name!r} 的 password 必须是字符串")
    if password_env is not None and (not isinstance(password_env, str) or not password_env):
        raise TestDbError(f"profile {name!r} 的 password_env 必须是非空字符串")
    if password is not None and password_env is not None:
        raise TestDbError(f"profile {name!r} 的 password 与 password_env 只能配置一个")

    normalized = dict(profile)
    normalized.update(
        {
            "name": name,
            "engine": "mysql",
            "host": host.strip(),
            "port": port,
            "username": username.strip(),
            "allowed_databases": allowed,
            "writable_databases": writable,
            "connect_timeout": int(profile.get("connect_timeout", 10)),
            "read_timeout": int(profile.get("read_timeout", 30)),
            "write_timeout": int(profile.get("write_timeout", 30)),
        }
    )
    for key in ("connect_timeout", "read_timeout", "write_timeout"):
        if not 1 <= normalized[key] <= 120:
            raise TestDbError(f"profile {name!r} 的 {key} 必须在 1..120 秒之间")
    return normalized


def load_profiles(base_dir: Path) -> dict[str, dict[str, Any]]:
    profile_file = base_dir / PROFILE_FILE_NAME
    if not profile_file.is_file():
        raise TestDbError(
            f"找不到测试库配置: {profile_file}\n"
            "请按 verify-test-db/references/configuration.md 创建并设置为 0600"
        )
    ensure_private_directory(base_dir)
    ensure_private_file(profile_file)
    try:
        payload = json.loads(profile_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TestDbError(f"测试库配置解析失败: {profile_file}: {exc}") from exc
    raw_profiles = payload.get("profiles") if isinstance(payload, dict) else None
    if not isinstance(raw_profiles, dict) or not raw_profiles:
        raise TestDbError("profiles.json 必须包含非空 profiles 对象")
    profiles: dict[str, dict[str, Any]] = {}
    for name, raw in raw_profiles.items():
        validate_identifier(name, "profile 名称")
        profiles[name] = validate_profile(name, raw)
    return profiles


def get_profile(base_dir: Path, name: str) -> dict[str, Any]:
    profiles = load_profiles(base_dir)
    if name not in profiles:
        raise TestDbError(f"未知 profile: {name}；可选值: {', '.join(sorted(profiles))}")
    return profiles[name]


def scope_allows(scope: Sequence[str], database: str) -> bool:
    return database.lower() not in SYSTEM_DATABASES and (tuple(scope) == ("*",) or database in scope)


def require_database(profile: Mapping[str, Any], database: str, *, write: bool = False) -> str:
    validate_identifier(database, "数据库名")
    field = "writable_databases" if write else "allowed_databases"
    if not scope_allows(profile[field], database):
        operation = "写入" if write else "读取"
        raise TestDbError(f"profile {profile['name']!r} 不允许{operation}数据库 {database!r}")
    return database


def profile_password(profile: Mapping[str, Any]) -> str:
    if profile.get("password") is not None:
        return str(profile["password"])
    password_env = profile.get("password_env")
    if password_env:
        value = os.environ.get(str(password_env))
        if value is None:
            raise TestDbError(f"未设置密码环境变量: {password_env}")
        return value
    return ""


def require_profile_configured(profile: Mapping[str, Any]) -> None:
    placeholders = [
        field
        for field, placeholder in PROFILE_PLACEHOLDERS.items()
        if profile.get(field) == placeholder
    ]
    if placeholders:
        raise TestDbError(
            f"profile {profile['name']!r} 仍包含安装模板占位符: {', '.join(placeholders)}；"
            "请先编辑 TEST_DB_DIR/profiles.json"
        )


def connect_mysql(profile: Mapping[str, Any], database: str | None, *, write: bool = False):
    require_profile_configured(profile)
    try:
        import pymysql
    except ImportError as exc:
        raise TestDbError("缺少 PyMySQL；请用项目 .venv 重新安装 requirements.txt") from exc

    kwargs: dict[str, Any] = {
        "host": profile["host"],
        "port": profile["port"],
        "user": profile["username"],
        "password": profile_password(profile),
        "database": database,
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor,
        "connect_timeout": profile["connect_timeout"],
        "read_timeout": profile["read_timeout"],
        "write_timeout": profile["write_timeout"],
        "autocommit": not write,
    }
    ssl_ca = profile.get("ssl_ca")
    if ssl_ca:
        kwargs["ssl"] = {"ca": str(Path(str(ssl_ca)).expanduser())}
    try:
        return pymysql.connect(**kwargs)
    except Exception as exc:
        raise TestDbError(
            f"连接测试库失败: profile={profile['name']} host={profile['host']}:{profile['port']} "
            f"database={database or '-'}: {exc}"
        ) from exc


def parse_sql(sql: str):
    if not isinstance(sql, str) or not sql.strip():
        raise TestDbError("SQL 不能为空")
    if "\x00" in sql:
        raise TestDbError("SQL 不得包含 NUL 字符")
    try:
        import sqlglot
    except ImportError as exc:
        raise TestDbError("缺少 sqlglot；请用项目 .venv 重新安装 requirements.txt") from exc
    try:
        expressions = sqlglot.parse(sql, read="mysql")
    except Exception as exc:
        raise TestDbError(f"SQL 解析失败: {exc}") from exc
    expressions = [expression for expression in expressions if expression is not None]
    if len(expressions) != 1:
        raise TestDbError("一次只能执行一条 SQL")
    return expressions[0]


def sql_kind(expression: Any) -> str:
    return expression.key.lower()


def iter_table_databases(expression: Any) -> Iterable[str]:
    try:
        from sqlglot import exp
    except ImportError as exc:
        raise TestDbError("缺少 sqlglot；请用项目 .venv 重新安装 requirements.txt") from exc
    for table in expression.find_all(exp.Table):
        database = table.db
        catalog = table.catalog
        if catalog:
            raise TestDbError("安全入口不支持带 catalog 的跨实例表引用")
        if database:
            yield str(database)


def require_referenced_databases(expression: Any, profile: Mapping[str, Any]) -> None:
    for database in iter_table_databases(expression):
        require_database(profile, database, write=False)


def validate_read_sql(sql: str, profile: Mapping[str, Any]) -> Any:
    expression = parse_sql(sql)
    kind = sql_kind(expression)
    if kind not in SAFE_READ_STATEMENTS:
        raise TestDbError(
            f"query-safe 只允许 {', '.join(sorted(SAFE_READ_STATEMENTS))}，实际为 {kind}"
        )
    if expression.args.get("locks"):
        raise TestDbError("query-safe 禁止 SELECT 行锁")
    lowered = sql.lower()
    blocked_patterns = {
        "into outfile": r"\binto\s+outfile\b",
        "into dumpfile": r"\binto\s+dumpfile\b",
        "for update": r"\bfor\s+update\b",
        "lock in share mode": r"\block\s+in\s+share\s+mode\b",
        "危险函数": r"\b(?:sleep|benchmark|get_lock|release_lock|load_file)\s*\(",
    }
    for label, pattern in blocked_patterns.items():
        if re.search(pattern, lowered, re.IGNORECASE):
            raise TestDbError(f"query-safe 禁止 {label}")
    require_referenced_databases(expression, profile)
    return expression


def write_target(expression: Any) -> Any:
    try:
        from sqlglot import exp
    except ImportError as exc:
        raise TestDbError("缺少 sqlglot；请用项目 .venv 重新安装 requirements.txt") from exc
    target = expression.this
    if isinstance(expression, exp.Insert) and isinstance(target, exp.Schema):
        target = target.this
    if not isinstance(target, exp.Table):
        raise TestDbError("写入计划只允许单表 INSERT、UPDATE 或 DELETE")
    if target.args.get("joins"):
        raise TestDbError("写入计划不允许多表 JOIN 修改")
    if isinstance(expression, exp.Delete) and (
        expression.args.get("tables") or expression.args.get("using")
    ):
        raise TestDbError("写入计划不允许多表 DELETE")
    return target


def validate_write_sql(sql: str, profile: Mapping[str, Any], default_database: str) -> Any:
    expression = parse_sql(sql)
    kind = sql_kind(expression)
    if kind not in SAFE_WRITE_STATEMENTS:
        raise TestDbError("写入计划只允许 INSERT、UPDATE 或 DELETE；禁止 DDL 和多语句")
    target = write_target(expression)
    target_database = str(target.db or default_database)
    if target_database != default_database:
        raise TestDbError(
            f"写入目标库 {target_database!r} 必须与 --database {default_database!r} 一致"
        )
    require_database(profile, target_database, write=True)
    if kind == "insert" and any(
        expression.args.get(field) for field in ("ignore", "conflict", "overwrite", "alternative")
    ):
        raise TestDbError("写入计划禁止 INSERT IGNORE、REPLACE 和 ON DUPLICATE KEY UPDATE")
    if kind in {"update", "delete"} and expression.args.get("where") is None:
        raise TestDbError(f"{kind.upper()} 必须包含 WHERE 条件")
    require_referenced_databases(expression, profile)
    return expression


def sql_routing_targets(sql: str, default_database: str) -> dict[str, tuple[str, ...]]:
    """Return physical database/table targets used to locate a unique instance."""
    validate_identifier(default_database, "数据库名")
    expression = parse_sql(sql)
    try:
        from sqlglot import exp
    except ImportError as exc:
        raise TestDbError("缺少 sqlglot；请用项目 .venv 重新安装 requirements.txt") from exc

    cte_names = {
        str(cte.alias)
        for cte in expression.find_all(exp.CTE)
        if str(cte.alias)
    }
    targets: dict[str, set[str]] = {default_database: set()}
    for table in expression.find_all(exp.Table):
        if table.catalog:
            raise TestDbError("安全入口不支持带 catalog 的跨实例表引用")
        table_name = str(table.name)
        database = str(table.db or default_database)
        if not table.db and table_name in cte_names:
            continue
        validate_identifier(database, "SQL 引用数据库名")
        validate_identifier(table_name, "SQL 引用表名")
        targets.setdefault(database, set()).add(table_name)
    return {
        database: tuple(sorted(tables))
        for database, tables in sorted(targets.items())
    }


def merge_routing_targets(
    *target_groups: Mapping[str, Sequence[str]],
) -> dict[str, tuple[str, ...]]:
    merged: dict[str, set[str]] = {}
    for targets in target_groups:
        for database, tables in targets.items():
            validate_identifier(database, "数据库名")
            for table in tables:
                validate_identifier(str(table), "表名")
                merged.setdefault(database, set()).add(str(table))
            merged.setdefault(database, set())
    return {
        database: tuple(sorted(tables))
        for database, tables in sorted(merged.items())
    }


def execute_query(
    profile: Mapping[str, Any], database: str, sql: str, limit: int
) -> dict[str, Any]:
    require_database(profile, database)
    if not 1 <= limit <= MAX_QUERY_ROWS:
        raise TestDbError(f"limit 必须在 1..{MAX_QUERY_ROWS} 之间")
    validate_read_sql(sql, profile)
    connection = connect_mysql(profile, database)
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql)
            if cursor.description is None:
                raise TestDbError("只读 SQL 没有返回结果集")
            rows = list(cursor.fetchmany(limit + 1))
            columns = [str(column[0]) for column in cursor.description]
    except TestDbError:
        raise
    except Exception as exc:
        raise TestDbError(
            f"查询失败: profile={profile['name']} database={database}: {exc}"
        ) from exc
    finally:
        connection.close()
    truncated = len(rows) > limit
    return {
        "columns": columns,
        "rows": [dict(row) for row in rows[:limit]],
        "truncated": truncated,
        "limit": limit,
    }


def list_databases(profile: Mapping[str, Any]) -> list[str]:
    connection = connect_mysql(profile, None)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SHOW DATABASES")
            rows = list(cursor.fetchall())
    except Exception as exc:
        raise TestDbError(f"读取数据库列表失败: profile={profile['name']}: {exc}") from exc
    finally:
        connection.close()
    databases: list[str] = []
    for row in rows:
        value = next(iter(row.values()))
        database = str(value)
        if scope_allows(profile["allowed_databases"], database):
            databases.append(database)
    return sorted(databases)


def list_tables(profile: Mapping[str, Any], database: str) -> list[str]:
    require_database(profile, database)
    connection = connect_mysql(profile, database)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SHOW TABLES")
            rows = list(cursor.fetchall())
    except Exception as exc:
        raise TestDbError(
            f"读取表列表失败: profile={profile['name']} database={database}: {exc}"
        ) from exc
    finally:
        connection.close()
    return sorted(str(next(iter(row.values()))) for row in rows)


def describe_table(profile: Mapping[str, Any], database: str, table: str) -> dict[str, Any]:
    require_database(profile, database)
    quoted_table = quote_identifier(table, "表名")
    connection = connect_mysql(profile, database)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"SHOW FULL COLUMNS FROM {quoted_table}")
            rows = list(cursor.fetchall())
            columns = [str(column[0]) for column in cursor.description]
    except Exception as exc:
        raise TestDbError(
            f"读取表结构失败: profile={profile['name']} database={database} table={table}: {exc}"
        ) from exc
    finally:
        connection.close()
    return {"columns": columns, "rows": [dict(row) for row in rows], "truncated": False}


def redaction_key(base_dir: Path) -> bytes:
    key_file = base_dir / "redaction.key"
    ensure_private_directory(base_dir)
    if key_file.exists():
        ensure_private_file(key_file)
        value = key_file.read_bytes()
        if len(value) < 32:
            raise TestDbError(f"脱敏密钥损坏: {key_file}")
        return value
    value = secrets.token_bytes(32)
    descriptor = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
    ensure_private_file(key_file)
    return value


def redact_value(key: bytes, value: Any) -> str:
    encoded = json.dumps(jsonable(value), ensure_ascii=False, sort_keys=True).encode("utf-8")
    digest = hmac.new(key, encoded, hashlib.sha256).hexdigest()[:16]
    return f"[REDACTED:{digest}]"


def redact_rows(base_dir: Path, rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    key: bytes | None = None
    result: list[dict[str, Any]] = []
    for row in rows:
        item: dict[str, Any] = {}
        for column, value in row.items():
            if value is not None and SENSITIVE_COLUMN_RE.search(str(column)):
                key = key or redaction_key(base_dir)
                item[str(column)] = redact_value(key, value)
            else:
                item[str(column)] = jsonable(value)
        result.append(item)
    return result


def artifact_path(base_dir: Path, category: str, artifact_id: str) -> Path:
    if not ARTIFACT_ID_RE.fullmatch(artifact_id):
        raise TestDbError(f"非法 artifact id: {artifact_id!r}")
    return base_dir / category / f"{artifact_id}.json"


def new_artifact_id(prefix: str) -> str:
    timestamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}-{timestamp}-{secrets.token_hex(4)}"


def load_artifact(base_dir: Path, category: str, artifact_id: str) -> dict[str, Any]:
    path = artifact_path(base_dir, category, artifact_id)
    if not path.is_file():
        raise TestDbError(f"找不到 {category} artifact: {artifact_id}")
    ensure_private_file(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TestDbError(f"artifact 解析失败: {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("id") != artifact_id:
        raise TestDbError(f"artifact 内容无效: {path}")
    return payload


def row_identity(row: Mapping[str, Any], key_columns: Sequence[str]) -> str:
    missing = [column for column in key_columns if column not in row]
    if missing:
        raise TestDbError(f"结果缺少对比主键列: {', '.join(missing)}")
    values = [row[column] for column in key_columns]
    return json.dumps(values, ensure_ascii=False, sort_keys=True, default=json_default)


def diff_rows(
    before_rows: Sequence[Mapping[str, Any]],
    after_rows: Sequence[Mapping[str, Any]],
    key_columns: Sequence[str],
) -> dict[str, Any]:
    before = {row_identity(row, key_columns): dict(row) for row in before_rows}
    after = {row_identity(row, key_columns): dict(row) for row in after_rows}
    if len(before) != len(before_rows) or len(after) != len(after_rows):
        raise TestDbError("对比主键不唯一；请增加 --key 列")

    added = [after[key] for key in sorted(after.keys() - before.keys())]
    removed = [before[key] for key in sorted(before.keys() - after.keys())]
    changed: list[dict[str, Any]] = []
    unchanged = 0
    for key in sorted(before.keys() & after.keys()):
        old = before[key]
        new = after[key]
        columns = sorted(set(old) | set(new))
        changes = {
            column: {"before": old.get(column), "after": new.get(column)}
            for column in columns
            if old.get(column) != new.get(column)
        }
        if changes:
            changed.append(
                {
                    "key": {column: new.get(column) for column in key_columns},
                    "changes": changes,
                }
            )
        else:
            unchanged += 1
    return {
        "summary": {
            "before": len(before_rows),
            "after": len(after_rows),
            "added": len(added),
            "removed": len(removed),
            "changed": len(changed),
            "unchanged": unchanged,
        },
        "added": added,
        "removed": removed,
        "changed": changed,
    }


def create_snapshot(
    base_dir: Path,
    profile: Mapping[str, Any],
    database: str,
    sql: str,
    limit: int,
    key_columns: Sequence[str],
    label: str,
) -> dict[str, Any]:
    if not key_columns:
        raise TestDbError("snapshot-safe 至少需要一个 --key")
    for column in key_columns:
        validate_identifier(column, "对比主键列")
    result = execute_query(profile, database, sql, limit)
    rows = redact_rows(base_dir, result["rows"])
    for row in rows:
        row_identity(row, key_columns)
    artifact_id = new_artifact_id("snap")
    snapshot = {
        "version": 1,
        "id": artifact_id,
        "created_at": utc_text(),
        "label": label,
        "profile": profile["name"],
        "database": database,
        "sql": sql,
        "limit": limit,
        "key_columns": list(key_columns),
        "columns": result["columns"],
        "rows": rows,
        "truncated": result["truncated"],
    }
    if snapshot["truncated"]:
        raise TestDbError("快照结果超过 limit；请缩小查询范围后再记录基线")
    atomic_write_json(artifact_path(base_dir, "snapshots", artifact_id), snapshot)
    return snapshot


def compare_snapshot(base_dir: Path, snapshot: Mapping[str, Any]) -> dict[str, Any]:
    profile = get_profile(base_dir, str(snapshot["profile"]))
    result = execute_query(
        profile,
        str(snapshot["database"]),
        str(snapshot["sql"]),
        int(snapshot["limit"]),
    )
    if result["truncated"]:
        raise TestDbError("当前结果超过快照 limit；无法生成可信差异")
    rows = redact_rows(base_dir, result["rows"])
    diff = diff_rows(snapshot["rows"], rows, snapshot["key_columns"])
    return {
        "snapshot_id": snapshot["id"],
        "checked_at": utc_text(),
        "profile": snapshot["profile"],
        "database": snapshot["database"],
        "key_columns": snapshot["key_columns"],
        **diff,
    }


def parse_expectation(value: str) -> tuple[str, Any]:
    if "=" not in value:
        raise TestDbError(f"--expect 必须使用 column=json_value: {value!r}")
    column, raw_value = value.split("=", 1)
    validate_identifier(column, "断言列")
    try:
        expected = json.loads(raw_value)
    except json.JSONDecodeError:
        expected = raw_value
    return column, expected


def expectations_pass(
    raw_rows: Sequence[Mapping[str, Any]],
    diff: Mapping[str, Any] | None,
    expectations: Sequence[tuple[str, Any]],
    changed_columns: Sequence[str],
    expected_row_count: int | None,
) -> tuple[bool, list[str]]:
    failures: list[str] = []
    if expected_row_count is not None and len(raw_rows) != expected_row_count:
        failures.append(f"row_count expected={expected_row_count} actual={len(raw_rows)}")
    for column, expected in expectations:
        if not raw_rows:
            failures.append(f"{column} assertion failed: no rows returned")
            continue
        mismatches = [
            row
            for row in raw_rows
            if column not in row or jsonable(row.get(column)) != expected
        ]
        if mismatches:
            failures.append(f"{column} assertion mismatch_count={len(mismatches)}")
    if changed_columns:
        if diff is None:
            failures.append("--expect-changed 需要 --snapshot")
        else:
            observed = {
                column
                for item in diff.get("changed", [])
                for column in item.get("changes", {})
            }
            for column in changed_columns:
                if column not in observed:
                    failures.append(f"expected changed column not observed: {column}")
    return not failures, failures


def verify_until(
    base_dir: Path,
    profile: Mapping[str, Any],
    database: str,
    sql: str,
    limit: int,
    expectations: Sequence[tuple[str, Any]],
    changed_columns: Sequence[str],
    expected_row_count: int | None,
    timeout: float,
    interval: float,
    snapshot: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if not expectations and not changed_columns and expected_row_count is None:
        raise TestDbError("verify-safe 至少需要 --expect、--expect-changed 或 --expect-row-count")
    if not 0 <= timeout <= MAX_VERIFY_TIMEOUT:
        raise TestDbError(f"timeout 必须在 0..{MAX_VERIFY_TIMEOUT:g} 秒之间")
    if not 0.1 <= interval <= 10:
        raise TestDbError("interval 必须在 0.1..10 秒之间")
    for column in changed_columns:
        validate_identifier(column, "变化断言列")
    if expected_row_count is not None and not 0 <= expected_row_count <= limit:
        raise TestDbError("expect-row-count 必须在 0..limit 之间")

    deadline = time.monotonic() + timeout
    attempts = 0
    last_payload: dict[str, Any] | None = None
    while True:
        attempts += 1
        result = execute_query(profile, database, sql, limit)
        if result["truncated"]:
            raise TestDbError("验证结果超过 limit；请缩小查询范围")
        redacted_rows = redact_rows(base_dir, result["rows"])
        diff = None
        if snapshot is not None:
            diff = diff_rows(snapshot["rows"], redacted_rows, snapshot["key_columns"])
        passed, failures = expectations_pass(
            result["rows"], diff, expectations, changed_columns, expected_row_count
        )
        last_payload = {
            "status": "PASS" if passed else "FAIL",
            "checked_at": utc_text(),
            "attempts": attempts,
            "profile": profile["name"],
            "database": database,
            "row_count": len(result["rows"]),
            "failures": failures,
            "rows": redacted_rows,
        }
        if diff is not None:
            last_payload["diff"] = diff
        if passed or time.monotonic() >= deadline:
            return last_payload
        time.sleep(min(interval, max(0.0, deadline - time.monotonic())))


def profile_fingerprint(profile: Mapping[str, Any]) -> str:
    identity = {
        "environment": profile["environment"],
        "engine": profile["engine"],
        "host": profile["host"],
        "port": profile["port"],
        "username": profile["username"],
        "allowed_databases": list(profile["allowed_databases"]),
        "writable_databases": list(profile["writable_databases"]),
    }
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def plan_confirmation_token(plan: Mapping[str, Any]) -> str:
    confirmed_content = {
        "id": plan["id"],
        "expires_at": plan["expires_at"],
        "profile": plan["profile"],
        "profile_fingerprint": plan["profile_fingerprint"],
        "host": plan["host"],
        "port": plan["port"],
        "database": plan["database"],
        "sql": plan["sql"],
        "rollback_sql": plan["rollback_sql"],
        "min_affected": plan["min_affected"],
        "max_affected": plan["max_affected"],
        "reason": plan["reason"],
    }
    encoded = json.dumps(confirmed_content, ensure_ascii=False, sort_keys=True).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()[:16]
    return f"{plan['id']}:{digest}"


@contextlib.contextmanager
def plan_execution_lock(base_dir: Path, artifact_id: str):
    plan_path = artifact_path(base_dir, "plans", artifact_id)
    ensure_private_directory(plan_path.parent)
    lock_path = plan_path.with_suffix(".lock")
    try:
        descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise TestDbError(
            f"plan 正在执行或上次执行状态不明: {artifact_id}；请先检查数据库，不要重复执行"
        ) from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(f"pid={os.getpid()} started_at={utc_text()}\n")
            handle.flush()
            os.fsync(handle.fileno())
        yield
    finally:
        if lock_path.exists():
            lock_path.unlink()


def create_write_plan(
    base_dir: Path,
    profile: Mapping[str, Any],
    database: str,
    sql: str,
    rollback_sql: str,
    max_affected: int,
    expires_minutes: int,
    reason: str,
    min_affected: int = 1,
) -> dict[str, Any]:
    require_profile_configured(profile)
    require_database(profile, database, write=True)
    validate_write_sql(sql, profile, database)
    validate_write_sql(rollback_sql, profile, database)
    if not 1 <= max_affected <= MAX_WRITE_ROWS:
        raise TestDbError(f"max-affected 必须在 1..{MAX_WRITE_ROWS} 之间")
    if not 0 <= min_affected <= max_affected:
        raise TestDbError("min-affected 必须在 0..max-affected 之间")
    if not 1 <= expires_minutes <= 1440:
        raise TestDbError("expires-minutes 必须在 1..1440 之间")
    if not reason.strip():
        raise TestDbError("写入计划必须说明 --reason")
    artifact_id = new_artifact_id("write")
    created = utc_now()
    plan = {
        "version": 1,
        "id": artifact_id,
        "status": "planned",
        "created_at": utc_text(created),
        "expires_at": utc_text(created + dt.timedelta(minutes=expires_minutes)),
        "profile": profile["name"],
        "profile_fingerprint": profile_fingerprint(profile),
        "host": profile["host"],
        "port": profile["port"],
        "database": database,
        "sql": sql,
        "rollback_sql": rollback_sql,
        "min_affected": min_affected,
        "max_affected": max_affected,
        "reason": reason.strip(),
    }
    plan["confirmation_token"] = plan_confirmation_token(plan)
    atomic_write_json(artifact_path(base_dir, "plans", artifact_id), plan)
    return plan


def parse_utc(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def route_cache_path(base_dir: Path) -> Path:
    return base_dir / ROUTE_CACHE_FILE_NAME


def empty_route_cache() -> dict[str, Any]:
    return {"version": 1, "routes": {}}


def load_route_cache(base_dir: Path) -> dict[str, Any]:
    path = route_cache_path(base_dir)
    if not os.path.lexists(path):
        return empty_route_cache()
    if path.is_symlink() or not path.is_file():
        raise TestDbError(f"路由缓存不是普通文件: {path}")
    ensure_private_file(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_route_cache()
    if not isinstance(payload, dict) or payload.get("version") != 1:
        return empty_route_cache()
    routes = payload.get("routes")
    if not isinstance(routes, dict):
        return empty_route_cache()
    return payload


def routing_cache_key(
    database: str,
    targets: Mapping[str, Sequence[str]],
    *,
    write: bool,
) -> str:
    identity = {
        "database": database,
        "targets": {
            name: sorted(str(table) for table in tables)
            for name, tables in sorted(targets.items())
        },
        "write": write,
    }
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def profile_can_route(
    profile: Mapping[str, Any],
    database: str,
    targets: Mapping[str, Sequence[str]],
    *,
    write: bool,
) -> bool:
    if write and not scope_allows(profile["writable_databases"], database):
        return False
    return all(
        scope_allows(profile["allowed_databases"], target_database)
        for target_database in targets
    )


def route_profile_fingerprints(
    profiles: Mapping[str, Mapping[str, Any]],
) -> dict[str, str]:
    return {
        name: profile_fingerprint(profile)
        for name, profile in sorted(profiles.items())
    }


def cached_route_candidates(
    cache: Mapping[str, Any],
    cache_key: str,
    fingerprints: Mapping[str, str],
) -> list[str] | None:
    routes = cache.get("routes")
    entry = routes.get(cache_key) if isinstance(routes, dict) else None
    if not isinstance(entry, dict) or entry.get("profiles") != dict(fingerprints):
        return None
    try:
        if utc_now() > parse_utc(str(entry["expires_at"])):
            return None
    except (KeyError, TypeError, ValueError):
        return None
    candidates = entry.get("candidates")
    if not isinstance(candidates, list) or any(
        not isinstance(name, str) or name not in fingerprints for name in candidates
    ):
        return None
    return candidates


def store_route_candidates(
    base_dir: Path,
    cache: dict[str, Any],
    cache_key: str,
    fingerprints: Mapping[str, str],
    candidates: Sequence[str],
) -> None:
    now = utc_now()
    routes = cache.setdefault("routes", {})
    routes[cache_key] = {
        "discovered_at": utc_text(now),
        "expires_at": utc_text(now + dt.timedelta(seconds=ROUTE_CACHE_TTL_SECONDS)),
        "profiles": dict(fingerprints),
        "candidates": list(candidates),
    }
    atomic_write_json(route_cache_path(base_dir), cache)


def discover_route_candidates(
    profiles: Mapping[str, Mapping[str, Any]],
    targets: Mapping[str, Sequence[str]],
) -> list[str]:
    database_candidates: list[str] = []
    failures: list[str] = []
    for name, profile in sorted(profiles.items()):
        try:
            require_profile_configured(profile)
            databases = set(list_databases(profile))
        except TestDbError as exc:
            failures.append(f"{name}: {exc}")
            continue
        if all(database in databases for database in targets):
            database_candidates.append(name)
    if failures:
        raise TestDbError(
            "自动定位数据库时部分候选实例探测失败，无法保证唯一实例: "
            + " | ".join(failures)
        )

    if len(database_candidates) <= 1 or not any(targets.values()):
        return database_candidates

    table_candidates: list[str] = []
    table_failures: list[str] = []
    for name in database_candidates:
        profile = profiles[name]
        matched = True
        for database, required_tables in targets.items():
            if not required_tables:
                continue
            try:
                actual_tables = set(list_tables(profile, database))
            except TestDbError as exc:
                table_failures.append(f"{name}/{database}: {exc}")
                matched = False
                break
            if not set(required_tables).issubset(actual_tables):
                matched = False
                break
        if matched:
            table_candidates.append(name)
    if table_failures:
        raise TestDbError(
            "自动定位数据表时部分候选实例探测失败，无法保证唯一实例: "
            + " | ".join(table_failures)
        )
    return table_candidates


def ambiguous_route_message(
    database: str,
    profiles: Mapping[str, Mapping[str, Any]],
    candidates: Sequence[str],
) -> str:
    choices = ", ".join(
        f"{name}({profiles[name]['host']}:{profiles[name]['port']})"
        for name in candidates
    )
    return (
        f"数据库 {database!r} 匹配多个测试实例: {choices}；"
        "请显式指定 --profile"
    )


def resolve_profile(
    base_dir: Path,
    database: str,
    *,
    profile_name: str | None = None,
    targets: Mapping[str, Sequence[str]] | None = None,
    write: bool = False,
    refresh: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    validate_identifier(database, "数据库名")
    if database.lower() in SYSTEM_DATABASES:
        raise TestDbError(f"禁止访问系统库: {database}")
    normalized_targets = merge_routing_targets(targets or {database: ()}, {database: ()})

    if profile_name:
        profile = get_profile(base_dir, profile_name)
        require_database(profile, database, write=write)
        for target_database in normalized_targets:
            require_database(profile, target_database)
        return profile, {
            "profile": profile["name"],
            "host": profile["host"],
            "port": profile["port"],
            "database": database,
            "source": "explicit",
            "write": write,
        }

    all_profiles = load_profiles(base_dir)
    considered = {
        name: profile
        for name, profile in all_profiles.items()
        if profile_can_route(
            profile,
            database,
            normalized_targets,
            write=write,
        )
    }
    if not considered:
        operation = "写入" if write else "读取"
        raise TestDbError(f"没有 profile 允许自动{operation}数据库 {database!r}")

    fingerprints = route_profile_fingerprints(considered)
    cache_key = routing_cache_key(database, normalized_targets, write=write)
    cache = load_route_cache(base_dir)
    candidates = None if refresh or write else cached_route_candidates(
        cache, cache_key, fingerprints
    )
    source = "cache"
    cache_warning: str | None = None
    if candidates is None:
        candidates = discover_route_candidates(considered, normalized_targets)
        try:
            store_route_candidates(base_dir, cache, cache_key, fingerprints, candidates)
            source = "discovery"
        except OSError as exc:
            source = "discovery-no-cache"
            cache_warning = f"路由缓存写入失败，本次仍使用实时发现结果: {exc}"

    if not candidates:
        target_text = ", ".join(normalized_targets)
        raise TestDbError(f"未在任何候选测试实例中找到数据库目标: {target_text}")
    if len(candidates) > 1:
        raise TestDbError(ambiguous_route_message(database, considered, candidates))

    profile = considered[candidates[0]]
    route = {
        "profile": profile["name"],
        "host": profile["host"],
        "port": profile["port"],
        "database": database,
        "source": source,
        "write": write,
    }
    if cache_warning:
        route["warning"] = cache_warning
    return profile, route


def print_auto_route(route: Mapping[str, Any]) -> None:
    print(
        "[route] "
        f"database={route['database']} profile={route['profile']} "
        f"host={route['host']}:{route['port']} source={route['source']}",
        file=sys.stderr,
    )
    if route.get("warning"):
        print(f"[route-warning] {route['warning']}", file=sys.stderr)


def _execute_write_plan_locked(
    base_dir: Path, artifact_id: str, confirmation: str, *, rollback: bool = False
) -> dict[str, Any]:
    plan = load_artifact(base_dir, "plans", artifact_id)
    expected_confirmation = plan_confirmation_token(plan)
    if plan.get("confirmation_token") != expected_confirmation:
        raise TestDbError("写入计划内容或确认摘要已被修改；请重新生成计划")
    if confirmation != expected_confirmation:
        raise TestDbError("--confirm 必须与计划输出的 confirmation_token 完全一致")
    expected_status = "applied" if rollback else "planned"
    if plan.get("status") != expected_status:
        raise TestDbError(
            f"plan 状态不允许当前操作: expected={expected_status} actual={plan.get('status')}"
        )
    if not rollback and utc_now() > parse_utc(str(plan["expires_at"])):
        raise TestDbError("写入计划已过期；请重新生成并再次确认")
    profile = get_profile(base_dir, str(plan["profile"]))
    if profile_fingerprint(profile) != plan.get("profile_fingerprint"):
        raise TestDbError("profile 在生成计划后发生变化；请重新生成写入计划")
    database = str(plan["database"])
    require_database(profile, database, write=True)
    sql = str(plan["rollback_sql"] if rollback else plan["sql"])
    validate_write_sql(sql, profile, database)
    max_affected = int(plan["max_affected"])
    min_affected = int(plan.get("min_affected", 1))

    intermediate_status = "rolling_back" if rollback else "applying"
    plan.update({"status": intermediate_status, "operation_started_at": utc_text()})
    atomic_write_json(artifact_path(base_dir, "plans", artifact_id), plan)

    try:
        connection = connect_mysql(profile, database, write=True)
        with connection.cursor() as cursor:
            affected = cursor.execute(sql)
        if affected < 0:
            raise TestDbError("数据库未返回可信的影响行数，已回滚")
        if affected < min_affected:
            raise TestDbError(
                f"影响行数 {affected} 低于 min_affected={min_affected}，已回滚"
            )
        if affected > max_affected:
            raise TestDbError(
                f"影响行数 {affected} 超过 max_affected={max_affected}，已回滚"
            )
        connection.commit()
    except Exception as exc:
        if "connection" in locals():
            try:
                connection.rollback()
            except Exception:
                pass
        plan.update(
            {
                "status": "rollback_failed" if rollback else "failed",
                "failed_at": utc_text(),
                "error": str(exc),
            }
        )
        atomic_write_json(artifact_path(base_dir, "plans", artifact_id), plan)
        if isinstance(exc, TestDbError):
            raise
        raise TestDbError(f"数据库写入失败并已尝试回滚: {exc}") from exc
    finally:
        if "connection" in locals():
            connection.close()

    now = utc_text()
    if rollback:
        plan.update({"status": "rolled_back", "rolled_back_at": now, "rollback_affected": affected})
        operation = "rollback"
    else:
        plan.update({"status": "applied", "applied_at": now, "affected_rows": affected})
        operation = "apply"
    atomic_write_json(artifact_path(base_dir, "plans", artifact_id), plan)
    return {
        "status": "OK",
        "operation": operation,
        "plan_id": artifact_id,
        "profile": plan["profile"],
        "host": plan["host"],
        "database": database,
        "affected_rows": affected,
        "plan_status": plan["status"],
    }


def execute_write_plan(
    base_dir: Path, artifact_id: str, confirmation: str, *, rollback: bool = False
) -> dict[str, Any]:
    with plan_execution_lock(base_dir, artifact_id):
        return _execute_write_plan_locked(
            base_dir,
            artifact_id,
            confirmation,
            rollback=rollback,
        )


def format_table(columns: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> str:
    rendered = [[json.dumps(jsonable(row.get(column)), ensure_ascii=False) for column in columns] for row in rows]
    widths = [len(column) for column in columns]
    for row in rendered:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))
    header = " | ".join(column.ljust(widths[index]) for index, column in enumerate(columns))
    separator = "-+-".join("-" * width for width in widths)
    body = [
        " | ".join(value.ljust(widths[index]) for index, value in enumerate(row))
        for row in rendered
    ]
    return "\n".join([header, separator, *body])


def print_payload(payload: Any, output_format: str = "json") -> None:
    if output_format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=json_default))
        return
    if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
        columns = payload.get("columns") or sorted(
            {str(column) for row in payload["rows"] for column in row}
        )
        print(format_table(columns, payload["rows"]))
        if payload.get("truncated"):
            print(f"\n[truncated at {payload.get('limit')} rows]", file=sys.stderr)
        return
    if isinstance(payload, list):
        print("\n".join(str(item) for item in payload))
        return
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=json_default))


def add_profile_argument(
    parser: argparse.ArgumentParser, *, required: bool = False
) -> None:
    parser.add_argument("--profile", required=required)


def add_routing_arguments(parser: argparse.ArgumentParser) -> None:
    add_profile_argument(parser)
    parser.add_argument(
        "--refresh-route",
        action="store_true",
        help="忽略 24 小时路由缓存并重新探测候选测试实例",
    )


def add_query_arguments(parser: argparse.ArgumentParser, *, keys: bool = False) -> None:
    add_routing_arguments(parser)
    parser.add_argument("--database", required=True)
    parser.add_argument("--sql", required=True)
    parser.add_argument("--limit", type=int, default=100)
    if keys:
        parser.add_argument("--key", action="append", required=True, dest="key_columns")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="查询、对比、验证并受控修改测试环境 MySQL 数据"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    profiles = subparsers.add_parser("profiles-safe", help="列出脱敏后的测试库 profiles")
    profiles.add_argument("--format", choices=("table", "json"), default="table")

    databases = subparsers.add_parser("databases-safe", help="列出允许读取的业务库")
    add_profile_argument(databases, required=True)
    databases.add_argument("--format", choices=("table", "json"), default="table")

    resolve = subparsers.add_parser("resolve-safe", help="自动定位数据库所在测试实例")
    resolve.add_argument("--database", required=True)
    resolve.add_argument("--table", action="append", default=[])
    resolve.add_argument("--for-write", action="store_true")
    resolve.add_argument("--refresh", action="store_true")
    resolve.add_argument("--format", choices=("table", "json"), default="table")

    tables = subparsers.add_parser("tables-safe", help="列出目标库中的表")
    add_routing_arguments(tables)
    tables.add_argument("--database", required=True)
    tables.add_argument("--format", choices=("table", "json"), default="table")

    describe = subparsers.add_parser("describe-safe", help="读取表字段结构")
    add_routing_arguments(describe)
    describe.add_argument("--database", required=True)
    describe.add_argument("--table", required=True)
    describe.add_argument("--format", choices=("table", "json"), default="table")

    query = subparsers.add_parser("query-safe", help="执行单条受限只读 SQL")
    add_query_arguments(query)
    query.add_argument("--format", choices=("table", "json"), default="table")

    snapshot = subparsers.add_parser("snapshot-safe", help="记录可重复查询的脱敏基线")
    add_query_arguments(snapshot, keys=True)
    snapshot.add_argument("--label", default="")

    compare = subparsers.add_parser("compare-safe", help="重查快照并输出字段差异")
    compare.add_argument("--snapshot", required=True)

    verify = subparsers.add_parser("verify-safe", help="轮询查询直到数据断言成立或超时")
    verify.add_argument("--snapshot")
    verify.add_argument("--profile")
    verify.add_argument("--refresh-route", action="store_true")
    verify.add_argument("--database")
    verify.add_argument("--sql")
    verify.add_argument("--limit", type=int, default=100)
    verify.add_argument("--expect", action="append", default=[])
    verify.add_argument("--expect-changed", action="append", default=[])
    verify.add_argument("--expect-row-count", type=int)
    verify.add_argument("--timeout", type=float, default=0)
    verify.add_argument("--interval", type=float, default=2)

    plan = subparsers.add_parser("write-plan-safe", help="生成不执行的受控写入计划")
    add_routing_arguments(plan)
    plan.add_argument("--database", required=True)
    plan.add_argument("--sql", required=True)
    plan.add_argument("--rollback-sql", required=True)
    plan.add_argument("--min-affected", type=int, default=1)
    plan.add_argument("--max-affected", type=int, default=1)
    plan.add_argument("--expires-minutes", type=int, default=30)
    plan.add_argument("--reason", required=True)

    apply = subparsers.add_parser("write-apply", help="确认并事务执行写入计划")
    apply.add_argument("--plan", required=True)
    apply.add_argument("--confirm", required=True)

    rollback = subparsers.add_parser("write-rollback", help="确认并执行计划中的回滚 SQL")
    rollback.add_argument("--plan", required=True)
    rollback.add_argument("--confirm", required=True)
    return parser


def public_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": profile["name"],
        "environment": profile["environment"],
        "engine": profile["engine"],
        "host": profile["host"],
        "port": profile["port"],
        "username": profile["username"],
        "allowed_databases": list(profile["allowed_databases"]),
        "writable_databases": list(profile["writable_databases"]),
        "configured": not any(
            profile.get(field) == placeholder
            for field, placeholder in PROFILE_PLACEHOLDERS.items()
        ),
        "password_source": (
            "env"
            if profile.get("password_env")
            else "file"
            if profile.get("password") is not None
            else "none"
        ),
    }


def resolve_for_command(
    base_dir: Path,
    args: argparse.Namespace,
    targets: Mapping[str, Sequence[str]],
    *,
    write: bool = False,
) -> dict[str, Any]:
    profile, route = resolve_profile(
        base_dir,
        args.database,
        profile_name=args.profile,
        targets=targets,
        write=write,
        refresh=bool(getattr(args, "refresh_route", False)),
    )
    if not args.profile:
        print_auto_route(route)
    return profile


def run_command(args: argparse.Namespace, base_dir: Path) -> int:
    command = args.command
    if command == "profiles-safe":
        rows = [public_profile(profile) for profile in load_profiles(base_dir).values()]
        print_payload({"columns": list(rows[0]) if rows else [], "rows": rows}, args.format)
        return 0

    if command == "databases-safe":
        profile = get_profile(base_dir, args.profile)
        rows = [{"database": database} for database in list_databases(profile)]
        print_payload({"columns": ["database"], "rows": rows}, args.format)
        return 0

    if command == "resolve-safe":
        targets = {args.database: tuple(args.table)}
        _, route = resolve_profile(
            base_dir,
            args.database,
            targets=targets,
            write=args.for_write,
            refresh=args.refresh,
        )
        route["tables"] = list(args.table)
        print_payload(route, args.format)
        return 0

    if command == "tables-safe":
        profile = resolve_for_command(base_dir, args, {args.database: ()})
        rows = [{"table": table} for table in list_tables(profile, args.database)]
        print_payload({"columns": ["table"], "rows": rows}, args.format)
        return 0

    if command == "describe-safe":
        profile = resolve_for_command(
            base_dir, args, {args.database: (args.table,)}
        )
        payload = describe_table(profile, args.database, args.table)
        print_payload(payload, args.format)
        return 0

    if command == "query-safe":
        profile = resolve_for_command(
            base_dir, args, sql_routing_targets(args.sql, args.database)
        )
        payload = execute_query(profile, args.database, args.sql, args.limit)
        payload["rows"] = redact_rows(base_dir, payload["rows"])
        print_payload(payload, args.format)
        return 0

    if command == "snapshot-safe":
        profile = resolve_for_command(
            base_dir, args, sql_routing_targets(args.sql, args.database)
        )
        payload = create_snapshot(
            base_dir,
            profile,
            args.database,
            args.sql,
            args.limit,
            args.key_columns,
            args.label,
        )
        print_payload(
            {
                "status": "OK",
                "snapshot_id": payload["id"],
                "created_at": payload["created_at"],
                "row_count": len(payload["rows"]),
                "profile": payload["profile"],
                "database": payload["database"],
                "key_columns": payload["key_columns"],
            }
        )
        return 0

    if command == "compare-safe":
        snapshot = load_artifact(base_dir, "snapshots", args.snapshot)
        print_payload(compare_snapshot(base_dir, snapshot))
        return 0

    if command == "verify-safe":
        snapshot = None
        if args.snapshot:
            if any(value is not None for value in (args.profile, args.database, args.sql)) or args.refresh_route:
                raise TestDbError(
                    "使用 --snapshot 时不要再传 --profile/--database/--sql/--refresh-route"
                )
            snapshot = load_artifact(base_dir, "snapshots", args.snapshot)
            profile = get_profile(base_dir, str(snapshot["profile"]))
            database = str(snapshot["database"])
            sql = str(snapshot["sql"])
            limit = int(snapshot["limit"])
        else:
            if not all((args.database, args.sql)):
                raise TestDbError("未使用 --snapshot 时必须提供 --database 和 --sql")
            if args.expect_changed:
                raise TestDbError("--expect-changed 只能与 --snapshot 一起使用")
            database = args.database
            sql = args.sql
            limit = args.limit
            profile = resolve_for_command(
                base_dir, args, sql_routing_targets(sql, database)
            )
        expectations = [parse_expectation(value) for value in args.expect]
        payload = verify_until(
            base_dir,
            profile,
            database,
            sql,
            limit,
            expectations,
            args.expect_changed,
            args.expect_row_count,
            args.timeout,
            args.interval,
            snapshot,
        )
        print_payload(payload)
        return 0 if payload["status"] == "PASS" else 2

    if command == "write-plan-safe":
        targets = merge_routing_targets(
            sql_routing_targets(args.sql, args.database),
            sql_routing_targets(args.rollback_sql, args.database),
        )
        profile = resolve_for_command(base_dir, args, targets, write=True)
        payload = create_write_plan(
            base_dir,
            profile,
            args.database,
            args.sql,
            args.rollback_sql,
            args.max_affected,
            args.expires_minutes,
            args.reason,
            args.min_affected,
        )
        print_payload(payload)
        return 0

    if command == "write-apply":
        print_payload(execute_write_plan(base_dir, args.plan, args.confirm))
        return 0
    if command == "write-rollback":
        print_payload(execute_write_plan(base_dir, args.plan, args.confirm, rollback=True))
        return 0
    raise TestDbError(f"未知命令: {command}")


def main(argv: Sequence[str] | None = None, *, base_dir: Path | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run_command(args, base_dir or runtime_directory())
    except TestDbError as exc:
        print(f"[!] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
