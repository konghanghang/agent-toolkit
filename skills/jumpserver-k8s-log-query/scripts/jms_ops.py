#!/usr/bin/env python3
"""JumpServer -> master kubectl -> worker host logs. Read-only."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any

COMMON_DIR = Path(__file__).resolve().parents[3] / "common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from runtime import load_runtime_or_exit

DEFAULT_ORG = "00000000-0000-0000-0000-000000000002"
DEFAULT_MASTER = "example-master"
DEFAULT_NAMESPACE = "test"
DEFAULT_LOG_ROOT = "/data/logs/{namespace}"
DEFAULT_SYSTEM_USER = "root"
WEBSOCKET_PROTOCOL = "JMS-KOKO"
ANSI_CSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
ANSI_OSC_RE = re.compile(r"\x1b\][^\x07]*(?:\x07|\x1b\\)")
ANSI_OTHER_RE = re.compile(r"\x1b[()][A-Z0-9]|\x1b[@-_]")
REPLICA_SUFFIX_RE = re.compile(r"-[a-z0-9]{7,10}-[a-z0-9]{5}$")
READ_ONLY_COMMANDS = {
    "ls",
    "stat",
    "du",
    "df",
    "find",
    "tail",
    "head",
    "grep",
    "egrep",
    "fgrep",
    "wc",
    "sort",
    "awk",
    "cat",
    "hostname",
    "uname",
    "pwd",
    "date",
    "whoami",
    "id",
    "findmnt",
    "mount",
    "echo",
    "true",
    "false",
    ":",
}
READ_ONLY_MULTI_COMMANDS = {
    "kubectl": {"get", "describe"},
    "crictl": {"ps", "pods", "inspect"},
    "nerdctl": {"ps"},
    "docker": {"ps"},
}
FORBIDDEN_RE = re.compile(
    r"\b(rm|mv|cp|touch|mkdir|rmdir|ln|truncate|tee|install|dd|chmod|chown|chgrp|"
    r"mkfs|umount|reboot|shutdown|systemctl|service|kill|pkill|killall|"
    r"kubectl\s+(delete|apply|patch|create|replace|scale|rollout|exec|edit)|"
    r"vi|vim|nano)\b",
    re.I,
)
UNSAFE_FIND_ACTION_RE = re.compile(
    r"(?:^|\s)-(?:delete|exec|execdir|ok|okdir|fprint|fprintf|fls)(?:\s|$)"
)
SAFE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?$")
SENSITIVE_HEADER_RE = re.compile(
    r"(?i)((?:\\?[\"'])?(?:authorization|proxy-authorization|cookie|set-cookie|"
    r"x-auth-token|x-api-key)(?:\\?[\"'])?\s*:\s*(?:\\?[\"'])?)"
    r"([^\"'\r\n,}]+)"
)


class JmsError(RuntimeError):
    pass


def jms_dir(runtime: dict[str, Any] | None = None) -> Path:
    if runtime is None:
        runtime = load_runtime_or_exit(__file__)
    path = Path(runtime["jms_dir"])
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise JmsError(f"JumpServer 运行目录不可用: {path} ({exc})") from exc
    return path


def creds_path(runtime: dict[str, Any] | None = None) -> Path:
    return jms_dir(runtime) / "jms-credentials.json"


def session_path(runtime: dict[str, Any] | None = None) -> Path:
    return jms_dir(runtime) / "jms_session.json"


def load_creds(runtime: dict[str, Any] | None = None) -> dict[str, Any]:
    path = creds_path(runtime)
    if not path.exists():
        raise JmsError(
            f"找不到凭证文件: {path}\n"
            "请按 skill 文档创建 jms-credentials.json"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    required = ("base_url", "username", "password")
    missing = [key for key in required if not data.get(key)]
    if missing:
        raise JmsError(f"凭证缺少字段: {', '.join(missing)}")
    data.setdefault("org_id", DEFAULT_ORG)
    data.setdefault("master_asset", DEFAULT_MASTER)
    data.setdefault("master_system_user", DEFAULT_SYSTEM_USER)
    data.setdefault("worker_system_user", DEFAULT_SYSTEM_USER)
    data.setdefault("default_namespace", DEFAULT_NAMESPACE)
    data.setdefault("log_root", DEFAULT_LOG_ROOT)
    return data


def load_session(runtime: dict[str, Any] | None = None) -> dict[str, Any] | None:
    path = session_path(runtime)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def save_session(payload: dict[str, Any], runtime: dict[str, Any] | None = None) -> Path:
    path = session_path(runtime)
    fd: int | None = None
    temporary_path: Path | None = None
    try:
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = None
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, path)
        temporary_path = None
    except OSError as exc:
        raise JmsError(f"无法写入 JumpServer 会话文件: {path} ({exc})") from exc
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass
    return path


def api_request(
    creds: dict[str, Any],
    path: str,
    method: str = "GET",
    data: dict[str, Any] | None = None,
    token: str | None = None,
) -> tuple[int, Any]:
    url = creds["base_url"].rstrip("/") + path
    headers = {"Accept": "application/json"}
    body = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["X-JMS-ORG"] = creds.get("org_id") or DEFAULT_ORG
    if data is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            parsed = json.loads(raw) if raw else {"detail": str(exc)}
        except json.JSONDecodeError:
            parsed = {"detail": raw.decode("utf-8", "replace")}
        return exc.code, parsed
    except (urllib.error.URLError, OSError) as exc:
        raise JmsError(f"JumpServer API 请求失败: {exc}") from exc


def encrypt_web_password(
    password: str,
    public_key_cookie: str,
    aes_key: bytes | None = None,
) -> str:
    """Reproduce JumpServer jumpserver.js encryptPassword()."""
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError:
        return encrypt_web_password_openssl(
            password, public_key_cookie, aes_key=aes_key
        )

    encoded_public_key = urllib.parse.unquote(public_key_cookie).strip('"')
    try:
        public_key_pem = base64.b64decode(encoded_public_key, validate=True)
        public_key = serialization.load_pem_public_key(public_key_pem)
    except (ValueError, TypeError) as exc:
        raise JmsError("JumpServer 登录页返回了无法解析的 RSA 公钥") from exc

    key_material = aes_key or secrets.token_urlsafe(12).encode("ascii")[:12]
    if not key_material:
        raise JmsError("网页密码加密所需的 AES key 为空")
    key_material = key_material[:16]
    aes_128_key = key_material.ljust(16, b"\0")
    plaintext = password.encode("utf-8")
    plaintext += b"\0" * ((-len(plaintext)) % 16)
    try:
        rsa_cipher = public_key.encrypt(key_material, rsa_padding.PKCS1v15())
        encryptor = Cipher(algorithms.AES(aes_128_key), modes.ECB()).encryptor()
        password_cipher = encryptor.update(plaintext) + encryptor.finalize()
    except (TypeError, ValueError) as exc:
        raise JmsError("无法按 JumpServer 网页协议加密密码") from exc
    return (
        base64.b64encode(rsa_cipher).decode("ascii")
        + ":"
        + base64.b64encode(password_cipher).decode("ascii")
    )


def encrypt_web_password_openssl(
    password: str,
    public_key_cookie: str,
    aes_key: bytes | None = None,
) -> str:
    openssl = shutil.which("openssl")
    if not openssl:
        raise JmsError(
            "本机缺少 cryptography 和 openssl，无法按 JumpServer 网页协议加密密码"
        )
    encoded_public_key = urllib.parse.unquote(public_key_cookie).strip('"')
    try:
        public_key_pem = base64.b64decode(encoded_public_key, validate=True)
    except (ValueError, TypeError) as exc:
        raise JmsError("JumpServer 登录页返回了无法解析的 RSA 公钥") from exc
    key_material = (aes_key or secrets.token_urlsafe(12).encode("ascii")[:12])[:16]
    if not key_material:
        raise JmsError("网页密码加密所需的 AES key 为空")
    aes_128_key = key_material.ljust(16, b"\0")
    plaintext = password.encode("utf-8")
    plaintext += b"\0" * ((-len(plaintext)) % 16)
    work = Path(tempfile.mkdtemp(prefix="jms-encrypt-"))
    try:
        key_file = work / "public.pem"
        key_file.write_bytes(public_key_pem)
        rsa_result = subprocess.run(
            [openssl, "pkeyutl", "-encrypt", "-pubin", "-inkey", str(key_file), "-pkeyopt", "rsa_padding_mode:pkcs1"],
            input=key_material,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if rsa_result.returncode != 0:
            raise JmsError("openssl 无法使用 JumpServer 登录页的 RSA 公钥")
        aes_result = subprocess.run(
            [openssl, "enc", "-aes-128-ecb", "-K", aes_128_key.hex(), "-nopad"],
            input=plaintext,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if aes_result.returncode != 0:
            raise JmsError("openssl 无法完成 JumpServer AES 密码加密")
        return (
            base64.b64encode(rsa_result.stdout).decode("ascii")
            + ":"
            + base64.b64encode(aes_result.stdout).decode("ascii")
        )
    finally:
        try:
            (work / "public.pem").unlink()
        except OSError:
            pass
        try:
            work.rmdir()
        except OSError:
            pass


def cookie_values(jar: CookieJar) -> dict[str, str]:
    return {cookie.name: cookie.value for cookie in jar}


def cookie_header(cookies: dict[str, str]) -> str:
    return "; ".join(f"{name}={value}" for name, value in cookies.items() if value)


def web_login(creds: dict[str, Any]) -> dict[str, str]:
    base = creds["base_url"].rstrip("/")
    login_url = base + "/core/auth/login/"
    jar = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    try:
        with opener.open(login_url, timeout=30) as resp:
            login_html = resp.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as exc:
        raise JmsError(f"无法打开 JumpServer 网页登录页: {exc}") from exc

    csrf_match = re.search(
        r'name=["\']csrfmiddlewaretoken["\'][^>]*value=["\']([^"\']+)',
        login_html,
        re.I,
    )
    cookies = cookie_values(jar)
    public_key_cookie = cookies.get("jms_public_key")
    if not csrf_match or not public_key_cookie:
        raise JmsError("JumpServer 网页登录页缺少 CSRF Token 或 RSA 公钥")

    form = urllib.parse.urlencode(
        {
            "csrfmiddlewaretoken": csrf_match.group(1),
            "username": creds["username"],
            "password": encrypt_web_password(creds["password"], public_key_cookie),
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        login_url,
        data=form,
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": base,
            "Referer": login_url,
        },
        method="POST",
    )
    try:
        with opener.open(request, timeout=30) as resp:
            final_url = resp.geturl()
            resp.read()
    except (urllib.error.URLError, OSError) as exc:
        raise JmsError(f"JumpServer 网页登录失败: {exc}") from exc
    if urllib.parse.urlparse(final_url).path.startswith("/core/auth/login/"):
        raise JmsError("JumpServer 网页登录失败：服务端仍停留在登录页")

    cookies = cookie_values(jar)
    if not cookies.get("jms_sessionid"):
        raise JmsError("JumpServer 网页登录未返回 jms_sessionid")
    cookies.setdefault("X-JMS-ORG", creds.get("org_id") or DEFAULT_ORG)
    return cookies


def web_request(
    creds: dict[str, Any],
    cookies: dict[str, str],
    path: str,
    accept: str = "application/json",
) -> tuple[int, bytes, str]:
    request = urllib.request.Request(
        creds["base_url"].rstrip("/") + path,
        headers={
            "Accept": accept,
            "Cookie": cookie_header(cookies),
            "X-JMS-ORG": creds.get("org_id") or DEFAULT_ORG,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            return resp.status, resp.read(), resp.geturl()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.geturl()
    except (urllib.error.URLError, OSError) as exc:
        raise JmsError(f"JumpServer Web Session 验证失败: {exc}") from exc


def web_session_valid(creds: dict[str, Any], cookies: dict[str, str]) -> bool:
    if not cookies.get("jms_sessionid"):
        return False
    status, _body, final_url = web_request(
        creds, cookies, "/api/v1/users/profile/"
    )
    return status == 200 and not urllib.parse.urlparse(final_url).path.startswith(
        "/core/auth/login/"
    )


def api_login(creds: dict[str, Any]) -> dict[str, Any]:
    status, payload = api_request(
        creds,
        "/api/v1/authentication/auth/",
        "POST",
        {"username": creds["username"], "password": creds["password"]},
    )
    if status >= 400 or not isinstance(payload, dict) or not payload.get("token"):
        raise JmsError(f"JumpServer API 登录失败 HTTP {status}: {payload}")
    return payload


def login(creds: dict[str, Any], runtime: dict[str, Any] | None = None) -> dict[str, Any]:
    cookies = web_login(creds)
    payload = api_login(creds)
    if not web_session_valid(creds, cookies):
        raise JmsError("JumpServer Web Session 验证失败")
    session = {
        "base": creds["base_url"].rstrip("/"),
        "token": payload["token"],
        "cookies": cookies,
        "keyword": payload.get("keyword") or "Bearer",
        "date_expired": payload.get("date_expired"),
        "username": payload.get("user", {}).get("username") or creds["username"],
        "saved_at": time.time(),
    }
    save_session(session, runtime)
    return session


def ensure_session(creds: dict[str, Any], runtime: dict[str, Any] | None = None) -> dict[str, Any]:
    session = load_session(runtime)
    if session and session.get("token") and isinstance(session.get("cookies"), dict):
        status, _ = api_request(creds, "/api/v1/users/profile/", token=session["token"])
        if status == 200 and web_session_valid(creds, session["cookies"]):
            return session
    return login(creds, runtime)


def list_assets(creds: dict[str, Any], token: str) -> list[dict[str, Any]]:
    status, payload = api_request(
        creds, "/api/v1/perms/users/assets/?limit=300", token=token
    )
    if status >= 400:
        raise JmsError(f"列出资产失败 HTTP {status}: {payload}")
    if isinstance(payload, dict):
        return list(payload.get("results") or [])
    if isinstance(payload, list):
        return payload
    return []


def system_users_for(creds: dict[str, Any], token: str, asset_id: str) -> list[dict[str, Any]]:
    status, payload = api_request(
        creds, f"/api/v1/perms/users/assets/{asset_id}/system-users/", token=token
    )
    if status >= 400:
        raise JmsError(f"列出系统用户失败 HTTP {status}: {payload}")
    return payload if isinstance(payload, list) else []


def pick_system_user(
    users: list[dict[str, Any]], preferred: str = DEFAULT_SYSTEM_USER
) -> dict[str, Any]:
    for user in users:
        if user.get("username") == preferred and user.get("protocol") == "ssh":
            return user
    available = ", ".join(
        str(user.get("username"))
        for user in users
        if user.get("protocol") == "ssh"
    ) or "无"
    raise JmsError(
        f"该资产没有指定的 SSH 系统用户 {preferred!r}；可用用户: {available}"
    )


def resolve_asset(assets: list[dict[str, Any]], hint: str) -> dict[str, Any]:
    hint = (hint or "").strip()
    if not hint:
        raise JmsError("资产名为空")
    for asset in assets:
        if asset.get("id") == hint or asset.get("hostname") == hint or asset.get("ip") == hint:
            return asset
    lowered = hint.lower()
    contains = [
        asset
        for asset in assets
        if lowered in str(asset.get("hostname") or "").lower()
        or lowered in str(asset.get("ip") or "")
    ]
    if len(contains) == 1:
        return contains[0]
    if contains:
        names = ", ".join(str(item.get("hostname")) for item in contains[:8])
        raise JmsError(f"资产名 {hint!r} 匹配到多台: {names}")
    raise JmsError(f"在 JumpServer 里找不到资产: {hint}")


def match_pods(rows: list[dict[str, str]], service: str) -> list[dict[str, str]]:
    service = service.strip()
    if not service:
        return []
    labeled = [
        row
        for row in rows
        if row.get("app_name") == service or row.get("app") == service
    ]
    if labeled:
        return labeled
    prefixed = [
        row
        for row in rows
        if row["name"] == service or row["name"].startswith(service + "-")
    ]
    preferred = []
    for row in prefixed:
        rest = row["name"][len(service) :]
        if rest == "" or REPLICA_SUFFIX_RE.match(rest):
            preferred.append(row)
    return preferred or prefixed


def parse_pod_table(text: str) -> list[dict[str, str]]:
    rows = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("___") or line.startswith("---"):
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        rows.append(
            {
                "name": parts[0],
                "phase": parts[1],
                "node": parts[2],
                "ip": parts[3] if len(parts) > 3 else "",
                "app_name": parts[4] if len(parts) > 4 else "",
                "app": parts[5] if len(parts) > 5 else "",
            }
        )
    return rows


def parse_node_table(text: str) -> dict[str, str]:
    mapping = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or "\t" not in line:
            continue
        name, ip = line.split("\t", 1)
        if name and ip:
            mapping[name.strip()] = ip.strip()
    return mapping


def extract_remote(text: str) -> str:
    if "___JMS_BEGIN___" in text and "___JMS_END___" in text:
        return text.split("___JMS_BEGIN___", 1)[1].split("___JMS_END___", 1)[0].strip()
    return text.strip()


def redact_sensitive_output(text: str) -> str:
    return SENSITIVE_HEADER_RE.sub(r"\1***", text)


def validate_remote_component(value: str, label: str) -> str:
    value = value.strip()
    if value in {"", ".", ".."} or not SAFE_COMPONENT_RE.fullmatch(value):
        raise JmsError(
            f"{label} 只允许字母、数字、点、下划线和连字符，且不能包含路径或 shell 语法"
        )
    return value


def is_read_only_command(command: str) -> bool:
    stripped = command.strip()
    if not stripped:
        return False
    if FORBIDDEN_RE.search(stripped) or UNSAFE_FIND_ACTION_RE.search(stripped):
        return False
    if any(token in stripped for token in ("\n", "\r", ";", "&&", "||", "`", "$(", "<", ">")):
        return False
    if re.search(r"\b(system|getline)\s*\(", stripped, re.I):
        return False
    chunks = [chunk.strip() for chunk in stripped.split("|") if chunk.strip()]
    if not chunks:
        return False
    for chunk in chunks:
        try:
            argv = shlex.split(chunk, posix=True)
        except ValueError:
            return False
        if not argv:
            return False
        executable = argv[0]
        if executable in READ_ONLY_MULTI_COMMANDS:
            if len(argv) < 2 or argv[1] not in READ_ONLY_MULTI_COMMANDS[executable]:
                return False
        elif executable not in READ_ONLY_COMMANDS:
            return False
        if executable in {"hostname", "mount", "date"} and len(argv) != 1:
            return False
        if executable == "find" and UNSAFE_FIND_ACTION_RE.search(" " + " ".join(argv[1:])):
            return False
        if executable == "sort" and any(
            arg == "-o"
            or arg.startswith("-o")
            or arg == "--output"
            or arg.startswith("--output=")
            or arg == "--compress-program"
            or arg.startswith("--compress-program=")
            for arg in argv[1:]
        ):
            return False
        if executable == "awk" and re.search(r"\b(system|getline)\b|\|&", " ".join(argv[1:]), re.I):
            return False
    return True


def terminal_endpoint(
    creds: dict[str, Any], token: str, asset_id: str
) -> dict[str, Any]:
    status, payload = api_request(
        creds,
        f"/api/v1/terminal/endpoints/smart/?protocol=http&asset_id={asset_id}",
        token=token,
    )
    if status >= 400 or not isinstance(payload, dict) or not payload.get("host"):
        raise JmsError(f"获取 JumpServer Web Terminal 端点失败 HTTP {status}: {payload}")
    return payload


def active_web_cookies(token: str) -> dict[str, str]:
    session = load_session()
    if (
        session
        and session.get("token") == token
        and isinstance(session.get("cookies"), dict)
        and session["cookies"].get("jms_sessionid")
    ):
        return session["cookies"]
    raise JmsError("本地缺少有效的 JumpServer Web Session，请先执行 jms login")


def websocket_target(
    creds: dict[str, Any], endpoint: dict[str, Any], path: str
) -> tuple[str, int, bool, str]:
    base = urllib.parse.urlparse(creds["base_url"])
    secure = base.scheme == "https"
    host = str(endpoint.get("host") or base.hostname or "")
    if not host:
        raise JmsError("JumpServer Web Terminal 端点没有主机地址")
    endpoint_port = endpoint.get("https_port" if secure else "http_port")
    default_port = 443 if secure else 80
    port = int(endpoint_port or base.port or default_port)
    query_path = path if path.startswith("/") else "/" + path
    return host, port, secure, query_path


def websocket_frame(opcode: int, payload: bytes, mask: bytes | None = None) -> bytes:
    mask = mask or os.urandom(4)
    if len(mask) != 4:
        raise ValueError("WebSocket mask must be four bytes")
    first = 0x80 | (opcode & 0x0F)
    length = len(payload)
    if length < 126:
        header = struct.pack("!BB", first, 0x80 | length)
    elif length <= 0xFFFF:
        header = struct.pack("!BBH", first, 0x80 | 126, length)
    else:
        header = struct.pack("!BBQ", first, 0x80 | 127, length)
    masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
    return header + mask + masked


class WebSocketClient:
    def __init__(
        self,
        host: str,
        port: int,
        path: str,
        origin: str,
        cookies: dict[str, str],
        secure: bool = False,
        timeout: int = 20,
    ) -> None:
        self.host = host
        self.port = port
        self.path = path
        self.origin = origin
        self.cookies = cookies
        self.secure = secure
        self.timeout = timeout
        self.sock: socket.socket | None = None
        self.buffer = bytearray()
        self.connected = False
        self.closed = False

    def connect(self) -> None:
        try:
            raw = socket.create_connection((self.host, self.port), timeout=self.timeout)
            if self.secure:
                raw = ssl.create_default_context().wrap_socket(raw, server_hostname=self.host)
            raw.settimeout(self.timeout)
            self.sock = raw
            key = base64.b64encode(os.urandom(16)).decode("ascii")
            host_header = self.host
            if self.port not in (80, 443):
                host_header += f":{self.port}"
            request = (
                f"GET {self.path} HTTP/1.1\r\n"
                f"Host: {host_header}\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n"
                f"Sec-WebSocket-Protocol: {WEBSOCKET_PROTOCOL}\r\n"
                f"Origin: {self.origin}\r\n"
                f"Cookie: {cookie_header(self.cookies)}\r\n"
                "\r\n"
            ).encode("ascii")
            raw.sendall(request)
            response = self._read_http_headers()
        except (OSError, ssl.SSLError) as exc:
            self.close()
            raise JmsError(f"连接 JumpServer Web Terminal 失败: {exc}") from exc

        status_line, headers = self._parse_http_headers(response)
        if " 101 " not in status_line:
            self.close()
            raise JmsError(
                f"JumpServer WebSocket 握手失败: {status_line}; "
                "若返回 302/login，说明 Web Session 已失效"
            )
        expected = base64.b64encode(
            hashlib.sha1(
                (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")
            ).digest()
        ).decode("ascii")
        if headers.get("sec-websocket-accept") != expected:
            self.close()
            raise JmsError("JumpServer WebSocket 握手返回了错误的 Sec-WebSocket-Accept")
        if headers.get("sec-websocket-protocol") != WEBSOCKET_PROTOCOL:
            self.close()
            raise JmsError("JumpServer WebSocket 未确认 JMS-KOKO 子协议")
        self.connected = True

    def _read_http_headers(self) -> bytes:
        while b"\r\n\r\n" not in self.buffer:
            chunk = self._recv_socket(4096)
            if not chunk:
                raise JmsError("JumpServer WebSocket 在握手完成前断开")
            self.buffer.extend(chunk)
            if len(self.buffer) > 65536:
                raise JmsError("JumpServer WebSocket 握手响应过大")
        head, rest = bytes(self.buffer).split(b"\r\n\r\n", 1)
        self.buffer = bytearray(rest)
        return head

    @staticmethod
    def _parse_http_headers(raw: bytes) -> tuple[str, dict[str, str]]:
        lines = raw.decode("iso-8859-1").split("\r\n")
        status_line = lines[0] if lines else ""
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if ":" in line:
                name, value = line.split(":", 1)
                headers[name.strip().lower()] = value.strip()
        return status_line, headers

    def _recv_socket(self, size: int) -> bytes:
        if self.sock is None:
            raise JmsError("JumpServer WebSocket 尚未连接")
        return self.sock.recv(size)

    def _read_exact(self, size: int) -> bytes:
        while len(self.buffer) < size:
            chunk = self._recv_socket(max(4096, size - len(self.buffer)))
            if not chunk:
                raise EOFError("JumpServer WebSocket 已断开")
            self.buffer.extend(chunk)
        data = bytes(self.buffer[:size])
        del self.buffer[:size]
        return data

    def send_frame(self, opcode: int, payload: bytes) -> None:
        if self.sock is None:
            raise JmsError("JumpServer WebSocket 尚未连接")
        self.sock.sendall(websocket_frame(opcode, payload))

    def send_json(self, payload: dict[str, Any]) -> None:
        self.send_frame(0x1, json.dumps(payload, separators=(",", ":")).encode("utf-8"))

    def recv_message(self) -> tuple[int, bytes]:
        fragments = bytearray()
        message_opcode: int | None = None
        while True:
            first, second = self._read_exact(2)
            final = bool(first & 0x80)
            opcode = first & 0x0F
            masked = bool(second & 0x80)
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read_exact(8))[0]
            mask = self._read_exact(4) if masked else b""
            payload = self._read_exact(length)
            if mask:
                payload = bytes(
                    value ^ mask[index % 4] for index, value in enumerate(payload)
                )
            if opcode == 0x8:
                self.closed = True
                raise EOFError("JumpServer WebSocket 已关闭会话")
            if opcode == 0x9:
                self.send_frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            if opcode in (0x1, 0x2):
                message_opcode = opcode
                fragments.extend(payload)
            elif opcode == 0x0 and message_opcode is not None:
                fragments.extend(payload)
            else:
                raise JmsError(f"JumpServer WebSocket 返回未知帧 opcode={opcode}")
            if final:
                return message_opcode or opcode, bytes(fragments)

    def close(self) -> None:
        sock = self.sock
        self.sock = None
        if sock is None:
            return
        if self.connected and not self.closed:
            try:
                sock.sendall(websocket_frame(0x8, struct.pack("!H", 1000)))
            except OSError:
                pass
        try:
            sock.close()
        except OSError:
            pass
        self.connected = False
        self.closed = True


def strip_terminal_controls(text: str) -> str:
    text = ANSI_OSC_RE.sub("", text)
    text = ANSI_CSI_RE.sub("", text)
    text = ANSI_OTHER_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    cleaned: list[str] = []
    for char in text:
        if char == "\b":
            if cleaned:
                cleaned.pop()
        elif char != "\x00":
            cleaned.append(char)
    return "".join(cleaned)


def command_wrapper(command: str, marker: str) -> str:
    command_b64 = base64.b64encode(command.encode("utf-8")).decode("ascii")
    marker_b64 = base64.b64encode(marker.encode("ascii")).decode("ascii")
    return (
        f"m=$(printf %s {marker_b64}|base64 -d); "
        f"printf '\\n%s\\n' \"$m\"; "
        f"printf %s {command_b64}|base64 -d|bash; "
        "r=$?; printf '\\n%s:%s\\n' \"$m\" \"$r\""
    )


def parse_command_result(text: str, marker: str) -> tuple[str, int] | None:
    cleaned = strip_terminal_controls(text)
    pattern = re.compile(
        rf"(?:^|\n){re.escape(marker)}\n(.*?)\n{re.escape(marker)}:(\d+)(?:\n|$)",
        re.S,
    )
    match = pattern.search(cleaned)
    if not match:
        return None
    return match.group(1), int(match.group(2))


def run_remote(
    creds: dict[str, Any],
    token: str,
    asset: dict[str, Any],
    command: str,
    timeout: int = 55,
    system_user_name: str = DEFAULT_SYSTEM_USER,
    cookies: dict[str, str] | None = None,
    trusted_internal: bool = False,
) -> str:
    if not trusted_internal and not is_read_only_command(command):
        raise JmsError(f"拒绝执行非只读命令: {command}")
    users = system_users_for(creds, token, asset["id"])
    system_user = pick_system_user(users, system_user_name)
    web_cookies = cookies or active_web_cookies(token)
    query = urllib.parse.urlencode(
        {
            "target_id": asset["id"],
            "type": "ssh",
            "system_user_id": system_user["id"],
        }
    )
    terminal_path = "/koko/terminal/?" + query
    status, _body, final_url = web_request(
        creds, web_cookies, terminal_path, accept="application/json,text/plain,*/*"
    )
    if status != 200 or urllib.parse.urlparse(final_url).path.startswith("/core/auth/login/"):
        raise JmsError(
            f"初始化 JumpServer Web Terminal 失败 HTTP {status}: "
            f"asset={asset.get('hostname')}"
        )
    endpoint = terminal_endpoint(creds, token, asset["id"])
    host, port, secure, ws_path = websocket_target(
        creds, endpoint, "/koko/ws/terminal/?" + query
    )
    ws = WebSocketClient(
        host,
        port,
        ws_path,
        creds["base_url"].rstrip("/"),
        web_cookies,
        secure=secure,
        timeout=min(timeout, 20),
    )
    connection_id = ""
    output = bytearray()
    marker = "__JMS_RESULT_" + secrets.token_hex(16) + "__"
    wrapper = command_wrapper(command, marker)
    deadline = time.monotonic() + timeout
    try:
        ws.connect()
        initialized = False
        command_sent = False
        while time.monotonic() < deadline:
            remaining = max(0.1, deadline - time.monotonic())
            if ws.sock is not None:
                ws.sock.settimeout(min(15.0, remaining))
            try:
                opcode, payload = ws.recv_message()
            except socket.timeout:
                if connection_id:
                    ws.send_json({"id": connection_id, "type": "PING", "data": ""})
                continue
            except EOFError as exc:
                raise JmsError(
                    f"JumpServer Web Terminal 在命令完成前断开: "
                    f"asset={asset.get('hostname')}"
                ) from exc
            if opcode == 0x2:
                output.extend(payload)
            else:
                try:
                    message = json.loads(payload.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                message_type = message.get("type")
                if message_type == "CONNECT":
                    connection_id = str(message.get("id") or "")
                    if not connection_id:
                        raise JmsError("JumpServer Web Terminal CONNECT 帧缺少连接 ID")
                    ws.send_json(
                        {
                            "id": connection_id,
                            "type": "TERMINAL_INIT",
                            "data": json.dumps({"cols": 116, "rows": 34}),
                        }
                    )
                    initialized = True
                elif message_type == "TERMINAL_SESSION" and initialized and not command_sent:
                    ws.send_json(
                        {"id": connection_id, "type": "TERMINAL_DATA", "data": wrapper}
                    )
                    ws.send_json(
                        {"id": connection_id, "type": "TERMINAL_DATA", "data": "\r"}
                    )
                    command_sent = True
                elif message_type == "TERMINAL_DATA":
                    data = message.get("data")
                    if isinstance(data, str):
                        output.extend(data.encode("utf-8"))
                elif message_type == "PING" and connection_id:
                    ws.send_json({"id": connection_id, "type": "PONG", "data": ""})
                elif message.get("err"):
                    raise JmsError(
                        f"JumpServer Web Terminal 返回错误: {message.get('err')}"
                    )
            result = parse_command_result(output.decode("utf-8", "replace"), marker)
            if result is not None:
                remote_output, remote_rc = result
                if remote_rc != 0:
                    raise JmsError(
                        f"远程只读命令执行失败: asset={asset.get('hostname')} "
                        f"remote_rc={remote_rc}"
                    )
                return remote_output
        raise JmsError(
            f"在 {asset.get('hostname')} 执行只读命令超时；"
            "未收到完整结果标记，不能把终端回显当成成功"
        )
    except (OSError, ssl.SSLError) as exc:
        raise JmsError(
            f"JumpServer Web Terminal 通信失败: asset={asset.get('hostname')} ({exc})"
        ) from exc
    finally:
        ws.close()


def remote_script(inner: str) -> str:
    return (
        "echo ___JMS_BEGIN___;\n(\n"
        + inner.strip()
        + "\n)\n__jms_inner_rc=$?\necho ___JMS_END___;\nexit $__jms_inner_rc;"
    )


def kubectl_on_master(
    creds: dict[str, Any],
    token: str,
    assets: list[dict[str, Any]],
    inner: str,
) -> str:
    master = resolve_asset(assets, creds.get("master_asset") or DEFAULT_MASTER)
    output = run_remote(
        creds,
        token,
        master,
        remote_script(inner),
        system_user_name=creds.get("master_system_user") or DEFAULT_SYSTEM_USER,
        trusted_internal=True,
    )
    return extract_remote(output)


def node_ip_map(creds: dict[str, Any], token: str, assets: list[dict[str, Any]]) -> dict[str, str]:
    text = kubectl_on_master(
        creds,
        token,
        assets,
        r"""kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.addresses[?(@.type=="InternalIP")].address}{"\n"}{end}'""",
    )
    return parse_node_table(text)


def resolve_node_asset(
    assets: list[dict[str, Any]],
    node_name: str,
    node_ips: dict[str, str],
) -> dict[str, Any]:
    ip = node_ips.get(node_name)
    if ip:
        try:
            return resolve_asset(assets, ip)
        except JmsError:
            pass
    return resolve_asset(assets, node_name)


def locate_service(
    creds: dict[str, Any],
    token: str,
    assets: list[dict[str, Any]],
    service: str,
    namespace: str,
) -> list[dict[str, str]]:
    service = validate_remote_component(service, "service")
    namespace = validate_remote_component(namespace, "namespace")
    inner = (
        f"kubectl get pods -n {shlex.quote(namespace)} -o "
        r"""jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.phase}{"\t"}{.spec.nodeName}{"\t"}{.status.podIP}{"\t"}{.metadata.labels.app\.kubernetes\.io/name}{"\t"}{.metadata.labels.app}{"\n"}{end}'"""
    )
    text = kubectl_on_master(creds, token, assets, inner)
    return match_pods(parse_pod_table(text), service)


def log_dir(creds: dict[str, Any], namespace: str, service: str) -> str:
    namespace = validate_remote_component(namespace, "namespace")
    service = validate_remote_component(service, "service")
    root = (creds.get("log_root") or DEFAULT_LOG_ROOT).format(namespace=namespace)
    return f"{root.rstrip('/')}/{service}"


def fetch_logs(
    creds: dict[str, Any],
    token: str,
    assets: list[dict[str, Any]],
    service: str,
    namespace: str,
    tail: int,
    filename: str | None = None,
) -> dict[str, Any]:
    service = validate_remote_component(service, "service")
    namespace = validate_remote_component(namespace, "namespace")
    if filename is not None:
        filename = validate_remote_component(filename, "file")
    pods = locate_service(creds, token, assets, service, namespace)
    if not pods:
        raise JmsError(f"namespace={namespace} 里没有匹配到服务 {service}")
    node_ips = node_ip_map(creds, token, assets)
    directory = log_dir(creds, namespace, service)
    quoted_directory = shlex.quote(directory)
    by_node: dict[str, list[dict[str, str]]] = {}
    for pod in pods:
        by_node.setdefault(pod["node"], []).append(pod)

    results = []
    for node_name, node_pods in by_node.items():
        try:
            asset = resolve_node_asset(assets, node_name, node_ips)
        except JmsError as exc:
            results.append(
                {
                    "node": node_name,
                    "asset": None,
                    "pods": node_pods,
                    "error": str(exc),
                }
            )
            continue
        if filename:
            quoted_file = shlex.quote(f"{directory}/{filename}")
            inner = f"""
ls -ld {quoted_directory} {quoted_file} 2>&1
echo ---TAIL---
if [ -f {quoted_file} ]; then tail -n {int(tail)} {quoted_file}; else echo MISSING_FILE; fi
"""
        else:
            inner = f"""
if [ ! -d {quoted_directory} ]; then echo MISSING_DIR; ls -ld {quoted_directory} 2>&1; exit 0; fi
ls -lah {quoted_directory}
echo ---FILES---
find {quoted_directory} -maxdepth 2 -type f | sort
echo ---TAIL---
newest=$(find {quoted_directory} -maxdepth 2 -type f \\( -name '*.log' -o -name '*.log.*' -o -name '*.out' -o -name '*.txt' \\) -printf '%T@ %p\\n' 2>/dev/null | sort -nr | awk '{{print $2}}' | head -n 3)
if [ -z "$newest" ]; then
  newest=$(find {quoted_directory} -maxdepth 2 -type f -printf '%T@ %p\\n' 2>/dev/null | sort -nr | awk '{{print $2}}' | head -n 3)
fi
if [ -z "$newest" ]; then
  echo NO_FILES
else
  for f in $newest; do
    echo "===== FILE $f ====="
    tail -n {int(tail)} "$f"
  done
fi
"""
        output = extract_remote(
            run_remote(
                creds,
                token,
                asset,
                remote_script(inner),
                system_user_name=creds.get("worker_system_user") or DEFAULT_SYSTEM_USER,
                trusted_internal=True,
            )
        )
        results.append(
            {
                "node": node_name,
                "asset": asset.get("hostname"),
                "ip": asset.get("ip"),
                "pods": node_pods,
                "directory": directory,
                "output": output,
            }
        )
    return {
        "service": service,
        "namespace": namespace,
        "directory": directory,
        "pods": pods,
        "nodes": results,
    }


def print_locate(pods: list[dict[str, str]], service: str, namespace: str) -> None:
    print(f"service={service} namespace={namespace} matches={len(pods)}")
    if not pods:
        return
    print(f"{'POD':<52} {'PHASE':<12} {'NODE':<16} IP")
    for pod in pods:
        print(f"{pod['name']:<52} {pod['phase']:<12} {pod['node']:<16} {pod.get('ip') or ''}")


def print_logs(payload: dict[str, Any]) -> None:
    print_locate(payload["pods"], payload["service"], payload["namespace"])
    print(f"log_dir={payload['directory']}")
    for item in payload["nodes"]:
        print()
        print("=" * 72)
        print(f"node={item['node']} asset={item.get('asset')} ip={item.get('ip')}")
        if item.get("error"):
            print(item["error"])
            continue
        print(redact_sensitive_output(item.get("output") or ""))


def cmd_login(_args: argparse.Namespace) -> int:
    runtime = load_runtime_or_exit(__file__)
    creds = load_creds(runtime)
    session = login(creds, runtime)
    print(f"login_ok user={session.get('username')} expired={session.get('date_expired')}")
    print(f"session={session_path(runtime)}")
    return 0


def cmd_nodes(_args: argparse.Namespace) -> int:
    runtime = load_runtime_or_exit(__file__)
    creds = load_creds(runtime)
    session = ensure_session(creds, runtime)
    assets = list_assets(creds, session["token"])
    mapping = node_ip_map(creds, session["token"], assets)
    print(f"{'K8S_NODE':<16} {'IP':<16} JMS_ASSET")
    for name, ip in sorted(mapping.items()):
        try:
            asset = resolve_node_asset(assets, name, mapping)
            label = f"{asset.get('hostname')} ({asset.get('ip')})"
        except JmsError as exc:
            label = f"UNMAPPED ({exc})"
        print(f"{name:<16} {ip:<16} {label}")
    return 0


def cmd_pods(args: argparse.Namespace) -> int:
    service = validate_remote_component(args.service, "service")
    runtime = load_runtime_or_exit(__file__)
    creds = load_creds(runtime)
    session = ensure_session(creds, runtime)
    assets = list_assets(creds, session["token"])
    namespace = validate_remote_component(
        args.namespace or creds.get("default_namespace") or DEFAULT_NAMESPACE,
        "namespace",
    )
    pods = locate_service(creds, session["token"], assets, service, namespace)
    print_locate(pods, service, namespace)
    return 0 if pods else 1


def cmd_logs(args: argparse.Namespace) -> int:
    service = validate_remote_component(args.service, "service")
    filename = (
        validate_remote_component(args.file, "file") if args.file is not None else None
    )
    runtime = load_runtime_or_exit(__file__)
    creds = load_creds(runtime)
    session = ensure_session(creds, runtime)
    assets = list_assets(creds, session["token"])
    namespace = validate_remote_component(
        args.namespace or creds.get("default_namespace") or DEFAULT_NAMESPACE,
        "namespace",
    )
    payload = fetch_logs(
        creds,
        session["token"],
        assets,
        service,
        namespace,
        tail=args.tail,
        filename=filename,
    )
    print_logs(payload)
    return 0


def cmd_exec(args: argparse.Namespace) -> int:
    if not is_read_only_command(args.command):
        raise JmsError(f"拒绝执行非只读命令: {args.command}")
    runtime = load_runtime_or_exit(__file__)
    creds = load_creds(runtime)
    session = ensure_session(creds, runtime)
    assets = list_assets(creds, session["token"])
    hint = args.asset or args.node
    if not hint:
        raise JmsError("请指定 --asset 或 --node")
    if args.node and not args.asset:
        mapping = node_ip_map(creds, session["token"], assets)
        asset = resolve_node_asset(assets, args.node, mapping)
    else:
        asset = resolve_asset(assets, hint)
    output = run_remote(
        creds,
        session["token"],
        asset,
        remote_script(args.command),
        system_user_name=args.system_user or DEFAULT_SYSTEM_USER,
        cookies=session["cookies"],
        trusted_internal=True,
    )
    print(redact_sensitive_output(extract_remote(output)))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="经 JumpServer 定位 test 命名空间的服务，并读取 worker 上 /data/logs/<ns>/<svc>",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("login", help="建立并验证 API Token 与 Web Session")

    p_pods = sub.add_parser("pods", help="只在 master 上定位服务所在节点")
    p_pods.add_argument("--service", required=True)
    p_pods.add_argument("--namespace")

    p_logs = sub.add_parser("logs", help="定位节点并读取对应 worker 日志")
    p_logs.add_argument("--service", required=True)
    p_logs.add_argument("--namespace")
    p_logs.add_argument("--tail", type=int, default=80)
    p_logs.add_argument("--file", help="只 tail 服务目录下的指定文件名")

    p_exec = sub.add_parser("exec", help="在指定资产或 K8s 节点上执行只读命令")
    p_exec.add_argument("--asset", help="JumpServer 资产名 / IP / ID")
    p_exec.add_argument("--node", help="kubectl 节点名，例如 k8s-work1")
    p_exec.add_argument("--command", required=True)
    p_exec.add_argument("--system-user", default=DEFAULT_SYSTEM_USER)

    sub.add_parser("nodes", help="列出 kubectl 节点与 JumpServer 资产的对应关系")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.cmd == "login":
            return cmd_login(args)
        if args.cmd == "nodes":
            return cmd_nodes(args)
        if args.cmd == "pods":
            return cmd_pods(args)
        if args.cmd == "logs":
            return cmd_logs(args)
        if args.cmd == "exec":
            return cmd_exec(args)
        parser.error(f"未知命令: {args.cmd}")
    except JmsError as exc:
        print(f"[!] {exc}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
