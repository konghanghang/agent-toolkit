from __future__ import annotations

import subprocess
import sys
from pathlib import Path


COMMON_DIR = Path(__file__).resolve().parents[1]
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from runtime import load_runtime_or_exit


def usage() -> str:
    return """用法:
  gitlab whoami
  gitlab search --query <project-name>
  gitlab resolve
  gitlab inspect --project <namespace/project> --ref <ref> --env <env> [--module <module>]
  gitlab trigger --project <namespace/project> --ref <ref> --env <env> [--module <module>] --plan-id <id> --confirm
  gitlab result --project <namespace/project> [--pipeline-id <id>|--ref <ref>] [--wait]

说明:
  - Python 和 GitLab 私有配置目录来自 ~/.local/agent-toolkit/work/runtime.json
  - 域名与 Token 来自 GITLAB_DIR/gitlab-credentials.json
  - 模糊项目名先用 search 消歧；trigger 必须携带 inspect 返回的 plan-id
  - trigger 是唯一写操作，默认等待最终结果；--no-wait 只触发不等待
  - result 是只读查询，可按 Pipeline ID 或 ref 最新一次读取
"""


def run_pipeline(argv: list[str]) -> int:
    runtime = load_runtime_or_exit(__file__)
    toolkit_root = Path(runtime["agent_toolkit_root"])
    script = toolkit_root / "skills" / "gitlab-run-pipeline" / "scripts" / "gitlab_pipeline.py"
    result = subprocess.run([runtime["python_bin"], str(script), *argv], check=False)
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    active_args = list(sys.argv[1:] if argv is None else argv)
    if not active_args or active_args[0] in {"help", "-h", "--help"}:
        print(usage(), end="")
        return 0
    return run_pipeline(active_args)


if __name__ == "__main__":
    raise SystemExit(main())
