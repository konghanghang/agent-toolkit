from __future__ import annotations

import base64
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


TOOLKIT_ROOT = Path(__file__).resolve().parents[3]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def server_frame(opcode: int, payload: bytes) -> bytes:
    first = 0x80 | opcode
    if len(payload) < 126:
        return struct.pack("!BB", first, len(payload)) + payload
    if len(payload) <= 0xFFFF:
        return struct.pack("!BBH", first, 126, len(payload)) + payload
    return struct.pack("!BBQ", first, 127, len(payload)) + payload


def decode_client_frame(frame: bytes) -> tuple[int, bytes]:
    opcode = frame[0] & 0x0F
    length = frame[1] & 0x7F
    offset = 2
    if length == 126:
        length = struct.unpack("!H", frame[offset : offset + 2])[0]
        offset += 2
    elif length == 127:
        length = struct.unpack("!Q", frame[offset : offset + 8])[0]
        offset += 8
    mask = frame[offset : offset + 4]
    offset += 4
    payload = frame[offset : offset + length]
    return opcode, bytes(value ^ mask[index % 4] for index, value in enumerate(payload))


class FakeSocket:
    def __init__(self, incoming: bytes = b""):
        self.incoming = bytearray(incoming)
        self.sent: list[bytes] = []
        self.closed = False

    def recv(self, size: int) -> bytes:
        if not self.incoming:
            return b""
        data = bytes(self.incoming[:size])
        del self.incoming[:size]
        return data

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def settimeout(self, _timeout: float) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class FakeWebSocket:
    def __init__(self, messages: list[tuple[int, bytes]]):
        self.messages = list(messages)
        self.sent: list[dict] = []
        self.connected = False
        self.closed = False
        self.sock = FakeSocket()

    def connect(self) -> None:
        self.connected = True

    def recv_message(self) -> tuple[int, bytes]:
        if not self.messages:
            raise EOFError("closed")
        return self.messages.pop(0)

    def send_json(self, payload: dict) -> None:
        self.sent.append(payload)

    def close(self) -> None:
        self.closed = True


class JmsWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ops = load_module(
            "jms_ops_test",
            TOOLKIT_ROOT / "skills/jumpserver-k8s-log-query/scripts/jms_ops.py",
        )
        cls.cli = load_module(
            "jms_cli_test",
            TOOLKIT_ROOT / "common/entrypoints/jms_cli.py",
        )

    def test_match_pods_prefers_replica_suffix_over_other_prefix(self):
        rows = [
            {"name": "ai-admin-7d9c8f6b5-abc12", "app_name": "", "app": ""},
            {"name": "ai-admin-ui-6f5d4c3b2-def34", "app_name": "", "app": ""},
            {"name": "ai-admin", "app_name": "", "app": ""},
        ]
        matched = self.ops.match_pods(rows, "ai-admin")
        self.assertEqual(
            ["ai-admin-7d9c8f6b5-abc12", "ai-admin"],
            [row["name"] for row in matched],
        )

    def test_safe_commands_fix_namespace_and_reject_unsafe_arguments(self):
        self.assertEqual(
            ["pods", "--service", "ai-flow-server", "--namespace", "test"],
            self.cli.resolve_safe_args(["pods-safe", "--service", "ai-flow-server"]),
        )
        self.assertEqual(
            ["logs", "--service", "data-center", "--namespace", "test", "--tail", "120"],
            self.cli.resolve_safe_args(["logs-safe", "--service", "data-center", "--tail", "120"]),
        )
        with self.assertRaises(SystemExit):
            self.cli.resolve_safe_args(["pods-safe", "--service", "../../etc"])
        with self.assertRaises(SystemExit):
            self.cli.resolve_safe_args(["logs-safe", "--service", "data-center", "--file", "app.log"])
        with self.assertRaises(SystemExit):
            self.cli.resolve_safe_args(["logs-safe", "--service", "data-center", "--tail", "501"])

    def test_match_pods_uses_labels_before_name_prefix(self):
        rows = [
            {"name": "data-center-foo", "app_name": "other", "app": ""},
            {"name": "dc-7d9c8f6b5-abc12", "app_name": "data-center", "app": ""},
        ]
        matched = self.ops.match_pods(rows, "data-center")
        self.assertEqual(["dc-7d9c8f6b5-abc12"], [row["name"] for row in matched])

    def test_read_only_command_allows_log_workflow_and_blocks_writes(self):
        self.assertTrue(
            self.ops.is_read_only_command(
                "find /data/logs/test/data-center -maxdepth 2 -type f | "
                "sort -nr | head -n 3"
            )
        )
        self.assertTrue(self.ops.is_read_only_command("kubectl get pods -n test"))
        self.assertFalse(self.ops.is_read_only_command("rm -rf /data/logs/test"))
        self.assertFalse(self.ops.is_read_only_command("kubectl delete pod x -n test"))
        self.assertFalse(self.ops.is_read_only_command("kubectl exec -it pod -- bash"))
        self.assertFalse(self.ops.is_read_only_command("systemctl restart docker"))
        self.assertFalse(self.ops.is_read_only_command("payload=$(rm -rf /data/logs/test)"))
        self.assertFalse(self.ops.is_read_only_command("if rm -rf /data/logs/test; then true; fi"))
        self.assertFalse(self.ops.is_read_only_command("hostname > /tmp/result"))
        self.assertFalse(self.ops.is_read_only_command("find /data/logs/test -delete"))
        self.assertFalse(self.ops.is_read_only_command("hostnamectl set-hostname changed"))
        self.assertFalse(self.ops.is_read_only_command("hostname changed"))
        self.assertFalse(self.ops.is_read_only_command("mount -o remount,rw /"))
        self.assertFalse(self.ops.is_read_only_command("date -s tomorrow"))
        self.assertFalse(
            self.ops.is_read_only_command(
                'echo $(python3 -c "open(\'/tmp/pwn\', \'w\').write(\'x\')")'
            )
        )
        self.assertFalse(
            self.ops.is_read_only_command(
                "awk 'BEGIN { system(\"python3 -c print(1)\") }' /dev/null"
            )
        )
        self.assertFalse(
            self.ops.is_read_only_command("sort --compress-program=python3 /tmp/input")
        )

    def test_remote_components_reject_paths_and_shell_syntax(self):
        self.assertEqual(
            "data-center",
            self.ops.validate_remote_component("data-center", "service"),
        )
        for value in ("../test", "data-center;id", "$(id)", "app/log.txt", ".."):
            with self.subTest(value=value), self.assertRaises(self.ops.JmsError):
                self.ops.validate_remote_component(value, "service")

    def test_sensitive_headers_are_redacted(self):
        text = (
            'header="{\\"Authorization\\":\\"Basic secret\\",'
            '\\"Cookie\\":\\"session=x\\",\\"Host\\":\\"svc:80\\"}"'
        )
        redacted = self.ops.redact_sensitive_output(text)
        self.assertNotIn("secret", redacted)
        self.assertNotIn("session=x", redacted)
        self.assertIn("Host", redacted)

    def test_remote_script_preserves_inner_exit_code(self):
        script = self.ops.remote_script("hostname")
        self.assertIn("__jms_inner_rc=$?", script)
        self.assertIn("exit $__jms_inner_rc", script)
        self.assertFalse(self.ops.is_read_only_command(script))

    def test_parse_tables_and_extract_remote(self):
        pods = self.ops.parse_pod_table(
            "___JMS_BEGIN___\n"
            "data-center-1\tRunning\tk8s-work1\t10.0.0.8\tdata-center\tdata-center\n"
            "---\n"
        )
        self.assertEqual("k8s-work1", pods[0]["node"])
        nodes = self.ops.parse_node_table(
            "k8s-work1\t192.0.2.101\nexample-worker-a\t192.0.2.181\n"
        )
        self.assertEqual("192.0.2.101", nodes["k8s-work1"])
        self.assertEqual(
            "hello",
            self.ops.extract_remote(
                "noise\n___JMS_BEGIN___\nhello\n___JMS_END___\nmore"
            ),
        )

    def test_resolve_asset_exact_and_ambiguous(self):
        assets = [
            {"id": "a1", "hostname": "k8s--w1--101", "ip": "192.0.2.101"},
            {"id": "a2", "hostname": "k8s--w2--102", "ip": "192.0.2.102"},
        ]
        self.assertEqual("a1", self.ops.resolve_asset(assets, "192.0.2.101")["id"])
        self.assertEqual("a2", self.ops.resolve_asset(assets, "k8s--w2--102")["id"])
        with self.assertRaises(self.ops.JmsError):
            self.ops.resolve_asset(assets, "k8s--w")

    def test_pick_system_user_requires_explicit_root(self):
        users = [
            {"id": "u94", "username": "work", "protocol": "ssh"},
            {"id": "root", "username": "root", "protocol": "ssh"},
        ]
        self.assertEqual("root", self.ops.pick_system_user(users, "root")["id"])
        with self.assertRaisesRegex(self.ops.JmsError, "没有指定.*root"):
            self.ops.pick_system_user(users[:1], "root")

    def test_log_dir_uses_namespace_template(self):
        creds = {"log_root": "/data/logs/{namespace}"}
        self.assertEqual(
            "/data/logs/test/data-center",
            self.ops.log_dir(creds, "test", "data-center"),
        )

    def test_save_session_is_atomic_and_wraps_permission_error(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            runtime = {"jms_dir": tmp_dir}
            payload = {"token": "secret", "cookies": {"jms_sessionid": "web"}}
            path = self.ops.save_session(
                payload, runtime
            )
            if os.name != "nt":
                self.assertEqual(0o600, path.stat().st_mode & 0o777)
            self.assertEqual(payload, json.loads(path.read_text(encoding="utf-8")))
            self.assertEqual([], list(Path(tmp_dir).glob(".jms_session.json.*.tmp")))
            with patch.object(
                self.ops.tempfile, "mkstemp", side_effect=PermissionError("denied")
            ):
                with self.assertRaisesRegex(
                    self.ops.JmsError, "无法写入 JumpServer 会话文件"
                ):
                    self.ops.save_session({"token": "secret"}, runtime)

    def test_save_session_preserves_previous_file_when_replace_fails(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            runtime = {"jms_dir": tmp_dir}
            path = Path(tmp_dir) / "jms_session.json"
            path.write_text('{"token": "previous"}\n', encoding="utf-8")
            os.chmod(path, 0o600)

            with patch.object(
                self.ops.os, "replace", side_effect=PermissionError("denied")
            ):
                with self.assertRaisesRegex(
                    self.ops.JmsError, "无法写入 JumpServer 会话文件"
                ):
                    self.ops.save_session({"token": "new"}, runtime)

            self.assertEqual(
                {"token": "previous"}, json.loads(path.read_text(encoding="utf-8"))
            )
            self.assertEqual([], list(Path(tmp_dir).glob(".jms_session.json.*.tmp")))

    def test_api_request_wraps_connection_errors(self):
        creds = {"base_url": "http://jms"}
        errors = (
            self.ops.urllib.error.URLError("offline"),
            TimeoutError("timed out"),
        )
        for error in errors:
            with self.subTest(error=type(error).__name__), patch.object(
                self.ops.urllib.request, "urlopen", side_effect=error
            ):
                with self.assertRaisesRegex(
                    self.ops.JmsError, "JumpServer API 请求失败"
                ):
                    self.ops.api_request(creds, "/api/v1/users/profile/")

    def test_login_saves_api_token_and_web_cookies(self):
        api_payload = {
            "token": "api-token",
            "keyword": "Bearer",
            "date_expired": "tomorrow",
            "user": {"username": "tester"},
        }
        with tempfile.TemporaryDirectory() as tmp_dir, patch.object(
            self.ops, "web_login", return_value={"jms_sessionid": "web-session"}
        ), patch.object(self.ops, "api_login", return_value=api_payload), patch.object(
            self.ops, "web_session_valid", return_value=True
        ) as session_valid:
            session = self.ops.login(
                {"base_url": "http://jms", "username": "tester", "password": "pw"},
                {"jms_dir": tmp_dir},
            )
            self.assertEqual("api-token", session["token"])
            self.assertEqual("web-session", session["cookies"]["jms_sessionid"])
            session_valid.assert_called_once()

    def test_login_rejects_invalid_web_session_without_saving(self):
        creds = {"base_url": "http://jms", "username": "tester", "password": "pw"}
        with patch.object(
            self.ops, "web_login", return_value={"jms_sessionid": "invalid"}
        ), patch.object(
            self.ops, "api_login", return_value={"token": "api-token"}
        ), patch.object(
            self.ops, "web_session_valid", return_value=False
        ), patch.object(self.ops, "save_session") as save:
            with self.assertRaisesRegex(self.ops.JmsError, "Web Session 验证失败"):
                self.ops.login(creds)

        save.assert_not_called()

    def test_web_login_rejects_redirect_back_to_login(self):
        public_key = "not-used-in-this-test"

        class Response:
            def __init__(self, url: str, body: bytes = b""):
                self.url = url
                self.body = body

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                pass

            def read(self):
                return self.body

            def geturl(self):
                return self.url

        class Opener:
            def __init__(self, jar):
                self.jar = jar
                self.calls = 0

            def open(self, _request, timeout=0):
                self.calls += 1
                if self.calls == 1:
                    self.jar.set_cookie(JmsWorkflowTest._cookie("jms_public_key", public_key))
                    html = b'<input name="csrfmiddlewaretoken" value="csrf">'
                    return Response("http://jms/core/auth/login/", html)
                return Response("http://jms/core/auth/login/")

        jar_holder = {}

        def build_opener(processor):
            jar_holder["jar"] = processor.cookiejar
            return Opener(processor.cookiejar)

        with patch.object(self.ops.urllib.request, "build_opener", side_effect=build_opener), patch.object(
            self.ops, "encrypt_web_password", return_value="encrypted"
        ):
            with self.assertRaisesRegex(self.ops.JmsError, "仍停留在登录页"):
                self.ops.web_login(
                    {"base_url": "http://jms", "username": "u", "password": "p"}
                )

    @staticmethod
    def _cookie(name, value):
        from http.cookiejar import Cookie

        return Cookie(
            version=0,
            name=name,
            value=value,
            port=None,
            port_specified=False,
            domain="jms",
            domain_specified=False,
            domain_initial_dot=False,
            path="/",
            path_specified=True,
            secure=False,
            expires=None,
            discard=True,
            comment=None,
            comment_url=None,
            rest={},
            rfc2109=False,
        )

    @classmethod
    def _public_key_cookie(cls):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.private_key = key
        pem = key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return base64.b64encode(pem).decode("ascii")

    def test_password_encryption_round_trip(self):
        if not self.ops.shutil.which("openssl"):
            self.skipTest("openssl is unavailable")
        key_dir = tempfile.TemporaryDirectory()
        self.addCleanup(key_dir.cleanup)
        private_path = Path(key_dir.name) / "private.pem"
        public_path = Path(key_dir.name) / "public.pem"
        encrypted_key_path = Path(key_dir.name) / "rsa.bin"
        encrypted_password_path = Path(key_dir.name) / "aes.bin"
        result = self.ops.subprocess.run(
            ["openssl", "genrsa", "-out", str(private_path), "2048"],
            capture_output=True,
        )
        self.assertEqual(0, result.returncode)
        result = self.ops.subprocess.run(
            ["openssl", "rsa", "-in", str(private_path), "-pubout", "-out", str(public_path)],
            capture_output=True,
        )
        self.assertEqual(0, result.returncode)
        cookie = base64.b64encode(public_path.read_bytes()).decode("ascii")
        aes_material = b"fixed-aes-key"
        encrypted = self.ops.encrypt_web_password(
            "secret-value", cookie, aes_material
        )
        rsa_part, aes_part = encrypted.split(":")
        encrypted_key_path.write_bytes(base64.b64decode(rsa_part))
        encrypted_password_path.write_bytes(base64.b64decode(aes_part))
        recovered_key = self.ops.subprocess.run(
            [
                "openssl", "pkeyutl", "-decrypt", "-inkey", str(private_path),
                "-pkeyopt", "rsa_padding_mode:pkcs1", "-in", str(encrypted_key_path),
            ],
            capture_output=True,
        )
        self.assertEqual(0, recovered_key.returncode)
        self.assertEqual(aes_material, recovered_key.stdout)
        aes_key = recovered_key.stdout[:16].ljust(16, b"\0")
        plaintext = self.ops.subprocess.run(
            [
                "openssl", "enc", "-d", "-aes-128-ecb", "-K", aes_key.hex(),
                "-nopad", "-in", str(encrypted_password_path),
            ],
            capture_output=True,
        )
        self.assertEqual(0, plaintext.returncode)
        self.assertEqual(b"secret-value", plaintext.stdout.rstrip(b"\0"))

    def test_websocket_client_handshake_success_and_close(self):
        key = base64.b64encode(b"0123456789abcdef").decode("ascii")
        accept = base64.b64encode(
            hashlib.sha1(
                (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")
            ).digest()
        ).decode("ascii")
        response = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n"
            "Sec-WebSocket-Protocol: JMS-KOKO\r\n\r\n"
        ).encode("ascii")
        sock = FakeSocket(response)
        with patch.object(self.ops.socket, "create_connection", return_value=sock), patch.object(
            self.ops.os, "urandom", side_effect=[b"0123456789abcdef", b"mask", b"mask"]
        ):
            ws = self.ops.WebSocketClient(
                "jms", 80, "/koko/ws/terminal/", "http://jms", {"jms_sessionid": "x"}
            )
            ws.connect()
            self.assertIn(b"Sec-WebSocket-Protocol: JMS-KOKO", sock.sent[0])
            ws.close()
        self.assertTrue(sock.closed)
        self.assertEqual(0x8, decode_client_frame(sock.sent[-1])[0])

    def test_websocket_handshake_rejects_redirect_accept_and_protocol(self):
        cases = [
            (b"HTTP/1.1 302 Found\r\nLocation: /core/auth/login/\r\n\r\n", "302"),
            (
                b"HTTP/1.1 101 Switching Protocols\r\nSec-WebSocket-Accept: wrong\r\nSec-WebSocket-Protocol: JMS-KOKO\r\n\r\n",
                "Sec-WebSocket-Accept",
            ),
        ]
        for response, expected in cases:
            with self.subTest(expected=expected), patch.object(
                self.ops.socket, "create_connection", return_value=FakeSocket(response)
            ), patch.object(
                self.ops.os,
                "urandom",
                side_effect=lambda size: b"0123456789abcdef" if size == 16 else b"mask",
            ):
                ws = self.ops.WebSocketClient(
                    "jms", 80, "/ws", "http://jms", {"jms_sessionid": "x"}
                )
                with self.assertRaisesRegex(self.ops.JmsError, expected):
                    ws.connect()

        key = base64.b64encode(b"0123456789abcdef").decode("ascii")
        accept = base64.b64encode(
            hashlib.sha1(
                (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")
            ).digest()
        ).decode("ascii")
        response = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n"
            "Sec-WebSocket-Protocol: wrong\r\n\r\n"
        ).encode("ascii")
        with patch.object(self.ops.socket, "create_connection", return_value=FakeSocket(response)), patch.object(
            self.ops.os,
            "urandom",
            side_effect=lambda size: b"0123456789abcdef" if size == 16 else b"mask",
        ):
            ws = self.ops.WebSocketClient(
                "jms", 80, "/ws", "http://jms", {"jms_sessionid": "x"}
            )
            with self.assertRaisesRegex(self.ops.JmsError, "JMS-KOKO"):
                ws.connect()

    def test_websocket_frames_cover_mask_ping_binary_and_large_lengths(self):
        for size in (5, 126, 66000):
            payload = b"x" * size
            frame = self.ops.websocket_frame(0x1, payload, b"mask")
            opcode, decoded = decode_client_frame(frame)
            self.assertEqual(0x1, opcode)
            self.assertEqual(payload, decoded)

        sock = FakeSocket(
            server_frame(0x9, b"ping")
            + server_frame(0x2, b"binary-output")
            + server_frame(0x8, struct.pack("!H", 1000))
        )
        ws = self.ops.WebSocketClient("jms", 80, "/ws", "http://jms", {})
        ws.sock = sock
        opcode, payload = ws.recv_message()
        self.assertEqual((0x2, b"binary-output"), (opcode, payload))
        self.assertEqual((0xA, b"ping"), decode_client_frame(sock.sent[0]))
        with self.assertRaises(EOFError):
            ws.recv_message()

    def test_parse_command_result_requires_complete_marker_and_rc(self):
        marker = "__JMS_RESULT_abc__"
        echo_only = self.ops.command_wrapper("hostname", marker)
        self.assertNotIn(marker, echo_only)
        self.assertIsNone(self.ops.parse_command_result(echo_only + "\nexample-worker-a\n", marker))
        self.assertIsNone(
            self.ops.parse_command_result(f"\n{marker}\nexample-worker-a\n{marker}\n", marker)
        )
        self.assertEqual(
            ("example-worker-a", 0),
            self.ops.parse_command_result(
                f"\x1b[32m\n{marker}\r\nexample-worker-a\r\n{marker}:0\r\n", marker
            ),
        )
        self.assertEqual(
            ("bad", 7),
            self.ops.parse_command_result(f"\n{marker}\nbad\n{marker}:7\n", marker),
        )

    def test_run_remote_protocol_and_strict_completion(self):
        marker = "__JMS_RESULT_fixed__"
        messages = [
            (0x1, json.dumps({"id": "cid", "type": "CONNECT", "data": ""}).encode()),
            (0x1, json.dumps({"id": "cid", "type": "TERMINAL_SESSION"}).encode()),
            (0x2, b"command echo only\r\n"),
            (0x2, f"\r\n{marker}\r\nexample-worker-a\r\n{marker}:0\r\n".encode()),
        ]
        fake = FakeWebSocket(messages)
        asset = {"id": "a1", "hostname": "master"}
        with patch.object(self.ops, "system_users_for", return_value=[
            {"id": "root-id", "username": "root", "protocol": "ssh"}
        ]), patch.object(self.ops, "web_request", return_value=(200, b"", "http://jms/koko/terminal/")), patch.object(
            self.ops, "terminal_endpoint", return_value={"host": "jms", "http_port": 80}
        ), patch.object(self.ops, "WebSocketClient", return_value=fake), patch.object(
            self.ops.secrets, "token_hex", return_value="fixed"
        ):
            output = self.ops.run_remote(
                {"base_url": "http://jms"},
                "token",
                asset,
                "hostname",
                cookies={"jms_sessionid": "web"},
            )
        self.assertEqual("example-worker-a", output)
        self.assertTrue(fake.connected)
        self.assertTrue(fake.closed)
        self.assertEqual("TERMINAL_INIT", fake.sent[0]["type"])
        self.assertEqual("TERMINAL_DATA", fake.sent[1]["type"])
        self.assertEqual("\r", fake.sent[2]["data"])

    def test_run_remote_fails_on_nonzero_or_close_without_marker(self):
        asset = {"id": "a1", "hostname": "master"}
        base_messages = [
            (0x1, json.dumps({"id": "cid", "type": "CONNECT"}).encode()),
            (0x1, json.dumps({"id": "cid", "type": "TERMINAL_SESSION"}).encode()),
        ]
        for extra, expected in [
            ([
                (0x2, b"\n__JMS_RESULT_fixed__\nbad\n__JMS_RESULT_fixed__:9\n")
            ], "remote_rc=9"),
            ([], "命令完成前断开"),
        ]:
            fake = FakeWebSocket(base_messages + extra)
            with self.subTest(expected=expected), patch.object(
                self.ops, "system_users_for", return_value=[
                    {"id": "root-id", "username": "root", "protocol": "ssh"}
                ]
            ), patch.object(self.ops, "web_request", return_value=(200, b"", "http://jms/koko/terminal/")), patch.object(
                self.ops, "terminal_endpoint", return_value={"host": "jms", "http_port": 80}
            ), patch.object(self.ops, "WebSocketClient", return_value=fake), patch.object(
                self.ops.secrets, "token_hex", return_value="fixed"
            ):
                with self.assertRaisesRegex(self.ops.JmsError, expected):
                    self.ops.run_remote(
                        {"base_url": "http://jms"},
                        "token",
                        asset,
                        "hostname",
                        cookies={"jms_sessionid": "web"},
                    )
                self.assertTrue(fake.closed)

    def test_kubectl_master_and_worker_system_users_are_explicit(self):
        assets = [{"id": "m", "hostname": "master", "ip": "1.1.1.1"}]
        captured = {}

        def run_remote(*_args, **kwargs):
            captured["user"] = kwargs["system_user_name"]
            return "___JMS_BEGIN___\nok\n___JMS_END___"

        with patch.object(self.ops, "run_remote", side_effect=run_remote):
            self.assertEqual(
                "ok",
                self.ops.kubectl_on_master(
                    {"master_asset": "master", "master_system_user": "root"},
                    "token",
                    assets,
                    "kubectl get pods",
                ),
            )
        self.assertEqual("root", captured["user"])

    def test_cli_usage_and_unknown_command(self):
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            code = self.cli.main([])
        self.assertEqual(0, code)
        self.assertIn("jms logs --service", stdout.getvalue())

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            code = self.cli.main(["scan"])
        self.assertEqual(1, code)
        self.assertIn("未知子命令", stdout.getvalue())

    def test_cli_requires_credentials(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            runtime = {
                "python_bin": "/tmp/python",
                "agent_toolkit_root": str(TOOLKIT_ROOT),
                "jms_dir": tmp_dir,
            }
            with patch.object(
                self.cli,
                "runtime_and_paths",
                return_value=(
                    runtime,
                    TOOLKIT_ROOT
                    / "skills/jumpserver-k8s-log-query/scripts/jms_ops.py",
                ),
            ), self.assertRaisesRegex(SystemExit, "找不到凭证文件"):
                self.cli.run_ops(["logs", "--service", "data-center"])

    def test_cli_lazily_creates_jms_runtime_directory(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            jms_dir = Path(tmp_dir) / "nested" / "jms"
            runtime = {
                "python_bin": "/tmp/python",
                "agent_toolkit_root": str(TOOLKIT_ROOT),
                "jms_dir": str(jms_dir),
            }
            with patch.object(
                self.cli,
                "runtime_and_paths",
                return_value=(
                    runtime,
                    TOOLKIT_ROOT
                    / "skills/jumpserver-k8s-log-query/scripts/jms_ops.py",
                ),
            ), self.assertRaisesRegex(SystemExit, "找不到凭证文件"):
                self.cli.run_ops(["logs", "--service", "data-center"])

            self.assertTrue(jms_dir.is_dir())


if __name__ == "__main__":
    unittest.main()
