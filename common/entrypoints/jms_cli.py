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


COMMANDS = ("login", "nodes", "pods", "logs", "exec", "pods-safe", "logs-safe")
SAFE_SERVICE_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
SAFE_NAMESPACE = "test"
SAFE_TAIL_MAX = 500


def usage() -> str:
    return """用法:
  jms login
  jms nodes
  jms pods --service data-center [--namespace test]
  jms logs --service data-center [--namespace test] [--tail 80] [--file app.log]
  jms pods-safe --service data-center
  jms logs-safe --service data-center [--tail 80]
  jms exec --asset example-master --system-user root --command 'hostname'
  jms exec --node k8s-work1 --command 'ls -lah /data/logs/test'

说明:
  - Python / AGENT_TOOLKIT_ROOT / TMP_DIR 来自 work 运行时配置
  - 未初始化时请先运行: python3 $AGENT_TOOLKIT_ROOT/common/bootstrap_work.py
  - 底层脚本来自 $AGENT_TOOLKIT_ROOT/skills/jumpserver-k8s-log-query/scripts
  - 凭证与 session 目录来自 work 运行时配置里的 jms_dir
  - 标准路径: 先上 master 用 kubectl 定位 NODE，再只去对应 worker 读 /data/logs/<ns>/<svc>
  - pods-safe/logs-safe 固定 test namespace，并限制服务名和 tail，供 Codex Rules 默认放行
  - 测试环境不要遍历全部 worker；应用 trace / ERROR 也继续走 JMS，不切换 SLS
  - 远程执行使用 JMS-KOKO Web Terminal，不回退 KoKo SSH :2222
  - 只允许只读命令，禁止改配置、删文件、kubectl apply/delete/exec
"""


def wants_help(argv: list[str]) -> bool:
    return any(arg in {"-h", "--help"} for arg in argv)


def runtime_and_paths() -> tuple[dict, Path]:
    runtime = load_runtime_or_exit(__file__)
    toolkit_root = Path(runtime["agent_toolkit_root"])
    script = toolkit_root / "skills" / "jumpserver-k8s-log-query" / "scripts" / "jms_ops.py"
    return runtime, script


def require_credentials(runtime: dict) -> None:
    runtime_dir = Path(runtime["jms_dir"])
    try:
        runtime_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SystemExit(f"[!] JMS_DIR 不可用: {runtime_dir} ({exc})") from exc
    cred_file = runtime_dir / "jms-credentials.json"
    if not cred_file.exists():
        raise SystemExit(f"[!] 找不到凭证文件: {cred_file}\n    请先按 skill 文档创建该文件")


def run_subprocess(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True)


def print_output(result: subprocess.CompletedProcess[str]) -> None:
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)


def resolve_safe_args(argv: list[str]) -> list[str]:
    cmd = argv[0]
    parser = argparse.ArgumentParser(prog=f"jms {cmd}", allow_abbrev=False)
    parser.add_argument("--service", required=True)
    if cmd == "logs-safe":
        parser.add_argument("--tail", type=int, default=80)
    args = parser.parse_args(argv[1:])
    if not SAFE_SERVICE_RE.fullmatch(args.service):
        parser.error("--service 只允许小写字母、数字、点和连字符")
    if cmd == "logs-safe" and not 1 <= args.tail <= SAFE_TAIL_MAX:
        parser.error(f"--tail 必须在 1-{SAFE_TAIL_MAX} 之间")
    resolved = ["pods" if cmd == "pods-safe" else "logs", "--service", args.service]
    resolved.extend(["--namespace", SAFE_NAMESPACE])
    if cmd == "logs-safe":
        resolved.extend(["--tail", str(args.tail)])
    return resolved


def run_ops(argv: list[str]) -> int:
    runtime, script = runtime_and_paths()
    if wants_help(argv) and (not argv or argv[0] in {"-h", "--help"} or argv[0] not in COMMANDS):
        print(usage(), end="")
        return 0
    if argv and argv[0] in COMMANDS and wants_help(argv[1:]):
        result = run_subprocess([runtime["python_bin"], str(script), argv[0], "--help"])
        print_output(result)
        return result.returncode
    require_credentials(runtime)
    result = run_subprocess([runtime["python_bin"], str(script), *argv])
    print_output(result)
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in {"help", "-h", "--help"}:
        print(usage(), end="")
        return 0
    if argv[0] not in COMMANDS:
        print(f"[!] 未知子命令: {argv[0]}")
        print(usage(), end="")
        return 1
    if argv[0] in {"pods-safe", "logs-safe"}:
        argv = resolve_safe_args(argv)
    return run_ops(argv)


if __name__ == "__main__":
    raise SystemExit(main())
