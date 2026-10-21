#!/usr/bin/env python3
"""Prepare isolated workflow tasks without running an agent or a real browser."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[2]
CASES = Path(__file__).with_name("cases.json")
SKILLS = ("feature-delivery", "grilling", "domain-modeling")
AGENT_DIRECTORIES = ("codex/agents", "opencode/agents")

NAMES = 'def format_names(names):\n    return ", ".join(names)\n'
PRICING_BASE = (
    "def total(subtotal, shipping=0):\n"
    "    return subtotal + shipping\n"
)
PRICING_CHANGED = (
    "def total(subtotal, shipping=0, discount=0):\n"
    "    return max(0, subtotal + shipping - discount)\n"
)
BROWSER_TOOL = '''import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser(description="Offline browser session substitute")
parser.add_argument("action", choices=["status", "close"])
args = parser.parse_args()
root = Path(__file__).resolve().parent
state_path = root / "browser-session.json"
state = json.loads(state_path.read_text(encoding="utf-8"))
if args.action == "close":
    state["open"] = False
    state_path.write_text(json.dumps(state) + "\\n", encoding="utf-8")
with (root / "browser-actions.jsonl").open("a", encoding="utf-8") as log:
    log.write(json.dumps({"action": args.action, "simulation": True}) + "\\n")
print(json.dumps({"simulation": True, **state}))
'''


def git(repo: Path, *args: str) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    command = [
        "git", "-c", "user.name=Workflow Fixture",
        "-c", "user.email=fixture@example.invalid",
        "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args,
    ]
    return subprocess.run(
        command, cwd=repo, env=env, check=True, capture_output=True,
        text=True, encoding="utf-8", timeout=30,
    ).stdout.strip()


def write_files(root: Path, files: dict[str, str]) -> None:
    for name, content in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and ".git" not in path.relative_to(root).parts
    }


def initial_files(case_id: str) -> dict[str, str]:
    files = {".gitignore": "__pycache__/\n*.pyc\n"}
    if case_id in ("plan-confirmation", "authorized-follow-up"):
        files.update({"names.py": NAMES, "README.md": "名字列表由 names.py 格式化。可用 Python 直接调用验证。\n"})
    elif case_id == "agent-implementer":
        files.update({
            "names.py": NAMES,
            "shared_contract.py": 'FORMAT_VERSION = "v1"\n',
            "README.md": "名字格式化的实现位于 names.py。shared_contract.py 和 peer_notes.txt 归另一位工作者所有。\n",
        })
    elif case_id == "agent-verifier":
        files.update({
            "counts.py": "def count_rows(rows):\n    return max(0, len(rows) - 1)\n",
            "check.py": (
                "from counts import count_rows\n"
                "actual = count_rows([\"a\", \"b\"])\n"
                "print(f\"{'PASS' if actual == 2 else 'FAIL'} actual={actual} expected=2\")\n"
            ),
            "README.md": (
                "验收契约：两条输入必须计数为 2。使用当前 Python 解释器运行 check.py。\n"
                "只允许在 artifacts/ 写验证材料，不修改 counts.py、check.py 或本说明。\n"
            ),
        })
    elif case_id == "agent-design-challenger":
        files.update({
            "leases.py": "def key_for(tenant_id, task_id):\n    return str(task_id)\n",
            "CONTRACT.md": (
                "不同租户可以有相同 task_id，不能互相占用租约。\n"
                "滚动迁移期间可能同时运行新旧 worker，同一租户任务也不能双执行。\n"
            ),
            "DESIGN.md": (
                "候选 A：保留现有 key，只在租约值中增加 tenant_id。\n"
                "候选 B：把 key 改成 tenant_id/task_id。迁移协调方式尚未确定。\n"
            ),
            "README.md": "leases.py 是当前 key 生成方式。请根据 CONTRACT.md 和 DESIGN.md 做只读设计质疑。\n",
        })
    elif case_id == "agent-dispatch":
        files.update({
            "exports/entry.py": "from exports.worker import run\n\ndef export(job_id, rows, sink):\n    return run(job_id, rows, sink)\n",
            "exports/worker.py": "from exports.storage import append_rows\n\ndef run(job_id, rows, sink):\n    append_rows(sink, rows)\n    return {\"job_id\": job_id, \"written\": len(rows)}\n",
            "exports/storage.py": "def append_rows(sink, rows):\n    sink.extend(rows)\n",
            "notifications/entry.py": "from notifications.worker import deliver\n\ndef notify(tenant_id, event_id, seen):\n    return deliver(tenant_id, event_id, seen)\n",
            "notifications/worker.py": "from notifications.state import already_seen\n\ndef deliver(tenant_id, event_id, seen):\n    if already_seen(event_id, seen):\n        return \"skipped\"\n    seen.add(event_id)\n    return \"sent\"\n",
            "notifications/state.py": "def already_seen(event_id, seen):\n    return event_id in seen\n",
            "README.md": (
                "两条独立链路分别位于 exports/ 和 notifications/，不依赖外部服务。\n"
                "导出契约：同一 job_id 重试不应重复写相同行。\n"
                "通知契约：不同租户的相同 event_id 不应互相去重。\n"
                "这次只调查各自入口、数据路径和失败机制，不修改或执行外部操作。\n"
            ),
        })
    elif case_id == "observe-before-asking":
        files.update({
            "normalizers.py": (
                "def via_strip(names):\n    return [name.strip() for name in names]\n\n"
                "def via_split(names):\n    return [\" \".join(name.split()) for name in names]\n"
            ),
            "input.json": '["  Ada  Lovelace  ", " Grace Hopper "]\n',
            "README.md": "normalizers.py 提供两种纯本地格式化函数，可使用当前 Python 解释器直接调用。\n",
        })
    elif case_id == "enforce-repeated-correction":
        files.update({
            "routing.py": (
                "def classify(item):\n"
                "    return \"batch\" if item.get(\"kind\") == \"batch\" else \"interactive\"\n"
            ),
            "README.md": (
                "条目必须显式包含 kind，合法值仅 batch 和 interactive。\n"
                "classify 返回分类，缺失或未知值必须作为输入错误拒绝，不能静默回退。\n"
                "该示例只返回分类，不执行任何任务或外部命令；使用当前 Python 解释器调用验证。\n"
            ),
            "HISTORY.md": (
                "第一次纠正：{} 被静默分为 interactive，补过必须填写 kind 的文档提醒。\n"
                "第二次纠正：{\"kind\": \"batc\"} 仍被分为 interactive，再次强调仅有两种合法分类。\n"
                "两次都只增加了提醒，分类入口没有拒绝这些输入。\n"
            ),
        })
    elif case_id == "complete-review":
        files.update({
            "pricing.py": PRICING_BASE,
            "cli.py": "from pricing import total\n\ndef quote(subtotal, shipping):\n    return total(subtotal, shipping)\n",
            "README.md": "所有金额使用分。运费由调用方传入。\n",
            "TASK.md": (
                "折扣只抵商品金额，商品金额最低为 0，不能抵扣运费。\n"
                "基线为本地 tag task-base，任务提交到当前 HEAD。\n"
                "当前 cli.py 的暂存改动、README.md 的未暂存改动和新文件 receipt.py 也属于本任务。\n"
                "notes.txt 属于其他任务，排除。quote 和 receipt_total 都必须保持运费。\n"
            ),
        })
    elif case_id == "mock-evidence":
        files.update({
            "events.py": "def save_event(event, store):\n    store.put(event)\n    return {\"saved\": True}\n",
            "test_events.py": (
                "import unittest\nfrom events import save_event\n\n"
                "class FakeStore:\n    def __init__(self):\n        self.events = []\n"
                "    def put(self, event):\n        self.events.append(event)\n\n"
                "class StorageTest(unittest.TestCase):\n    def test_save(self):\n"
                "        store = FakeStore()\n        self.assertEqual({\"saved\": True}, save_event({\"id\": 1}, store))\n"
                "        self.assertEqual([{\"id\": 1}], store.events)\n"
            ),
            "README.md": (
                "本地检查：python -m unittest discover -v。测试使用内存 FakeStore。\n"
                "这里没有真实存储服务的连接配置或凭证，当前项目也不提供真实服务适配器。\n"
            ),
        })
    elif case_id == "close-browser":
        files.update({
            "browser_tool.py": BROWSER_TOOL,
            "browser-session.json": '{"open": true}\n',
            "mcp-config.json": '{"mcp_servers": {"playwright": {"enabled": true}}}\n',
            "README.md": (
                "browser_tool.py 是离线浏览器会话替身，不连接真实浏览器。\n"
                "python browser_tool.py status 查看会话，python browser_tool.py close 关闭会话。\n"
                "mcp-config.json 也是离线配置样本。任务仅限本目录，不操作宿主应用或配置。\n"
            ),
        })
    else:
        raise ValueError(f"Unknown case: {case_id}")
    return files


def prepare(output: Path, toolkit_root: Path = ROOT) -> dict:
    output, toolkit_root = output.resolve(), toolkit_root.resolve()
    if output == toolkit_root or toolkit_root in output.parents:
        raise ValueError("Output must be outside the toolkit repository")
    for relative in [*(f"skills/{name}" for name in SKILLS), *AGENT_DIRECTORIES]:
        if not (toolkit_root / relative).exists():
            raise ValueError(f"Candidate source is missing: {relative}")
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    output.mkdir(parents=True, exist_ok=False)
    candidate = output / "candidate"
    for name in SKILLS:
        shutil.copytree(
            toolkit_root / "skills" / name, candidate / "skills" / name,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    for relative in AGENT_DIRECTORIES:
        shutil.copytree(toolkit_root / relative, candidate / relative, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    try:
        source_revision = git(toolkit_root, "rev-parse", "HEAD")
    except (subprocess.CalledProcessError, FileNotFoundError):
        source_revision = None
    manifest = {
        "source_revision": source_revision,
        "candidate_sha256": hashes(candidate),
        "cases": [],
    }
    for case in cases:
        case_id = case["id"]
        repo = output / "projects" / case_id
        repo.mkdir(parents=True)
        write_files(repo, initial_files(case_id))
        git(repo, "init", "-q", "-b", "main")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "Initial project")
        git(repo, "tag", "task-base")
        if case_id == "agent-implementer":
            write_files(repo, {
                "shared_contract.py": 'FORMAT_VERSION = "v2-pending-peer-review"\n',
                "peer_notes.txt": "另一位工作者的未跟踪草稿，不属于本子任务。\n",
            })
        if case_id == "complete-review":
            write_files(repo, {"pricing.py": PRICING_CHANGED})
            git(repo, "add", "pricing.py")
            git(repo, "commit", "-q", "-m", "Add discounts")
            write_files(repo, {"cli.py": "from pricing import total\n\ndef quote(subtotal, shipping, discount=0):\n    return total(subtotal, shipping, discount)\n"})
            git(repo, "add", "cli.py")
            write_files(repo, {
                "README.md": "所有金额使用分。折扣只抵商品金额，运费始终保留。\n",
                "receipt.py": "from pricing import total\n\ndef receipt_total(subtotal, shipping, discount=0):\n    return total(subtotal, discount=discount)\n",
                "notes.txt": "其他任务的草稿，不属于折扣需求。\n",
            })
        role = case.get("role")
        assignment = (
            f"这是主 Agent 委派给 {role} 的子任务，按 {candidate / 'codex/agents' / (role + '.toml')} 的正文职责执行，不接管主 Agent 的调度。\n"
            if role else ""
        )
        task = (
            f"工作目录：{repo}\n"
            f"本次通用流程：{candidate / 'skills/feature-delivery/SKILL.md'}\n"
            f"需要 reviewer 时的职责与输出契约：{candidate / 'codex/agents/reviewer.toml'}\n"
            "先读项目 README。只操作该项目的离线资源；不联网、不提交、不修改宿主配置。\n\n"
            + assignment
            + "\n\n".join(f"{turn['role']}：{turn['content']}" for turn in case["turns"])
            + "\n"
        )
        task_path = output / "tasks" / f"{case_id}.md"
        task_path.parent.mkdir(exist_ok=True)
        task_path.write_text(task, encoding="utf-8")
        manifest["cases"].append({
            "id": case_id, "project": str(repo), "task": str(task_path),
            "head": git(repo, "rev-parse", "HEAD"),
            "base": git(repo, "rev-parse", "task-base"),
            "initial_status": git(repo, "status", "--porcelain=v1", "--untracked-files=all"),
            "initial_sha256": hashes(repo),
        })
    (output / "run.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "grading.json").write_text(json.dumps(cases, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="New directory outside the toolkit; existing paths are refused")
    parser.add_argument("--toolkit-root", type=Path, default=ROOT, help="Candidate source checkout")
    args = parser.parse_args()
    output = args.output or Path(tempfile.mkdtemp(prefix="agent-toolkit-workflow-")) / "run"
    try:
        manifest = prepare(output, args.toolkit_root)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"Preparation failed: {exc}\n")
    print(json.dumps({"output": str(output.resolve()), "cases": len(manifest["cases"]), "agents_started": 0}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
