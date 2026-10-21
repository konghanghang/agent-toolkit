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
SKILLS = (
    "feature-delivery", "grilling", "domain-modeling", "work-feature-delivery",
    "tapd-story", "git-commit",
)
BUSINESS_CASES = {
    "work-small-fix", "work-resume-materials", "work-pending-batch",
    "work-readonly-analysis", "work-missing-requirement-id",
}
ENTRY_CASES = {
    "work-topic-clarification", "work-discussion-confirmed", "work-create-only",
    "work-authorized-refactor", "work-existing-review",
}
BUSINESS_CASES |= ENTRY_CASES
BASELINE_BRANCH_CASES = {
    "work-readonly-analysis", "work-missing-requirement-id",
    *ENTRY_CASES - {"work-authorized-refactor"},
}
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


LABEL_BASE = "def normalize_label(value):\n    return value\n"
LABEL_TEST = (
    "import unittest\nfrom app import normalize_label\n\n"
    "class LabelTest(unittest.TestCase):\n"
    "    def test_labels(self):\n"
    "        for value, expected in [('  Ada  Lovelace  ', 'Ada  Lovelace'), (' Grace ', 'Grace'), ('   ', ''), ('', '')]:\n"
    "            with self.subTest(value=value):\n"
    "                self.assertEqual(expected, normalize_label(value))\n"
)
REQUIREMENT_TEMPLATE = """---
requirementId: "<ID>"
title: <名称>
phase: 待开发
owner: 待确认
updated: <yyyy-MM-dd>
blocker: 无
nextStep: <下一步>
---

<RequirementStatus />

# <名称>

## 来源与范围

## 影响范围与验收入口

## 处理方案

## 测试验收

## 实施与验证记录

## 关联材料与下一步
"""
RELEASE_TEMPLATE = """# <yyyyMMdd> 上线清单

## 一页摘要

## 发布服务

## 变更物料

## 代码合入摘要

## 发布顺序

## 上线卡点

## 发布后冒烟

## 回滚
"""
EXISTING_RELEASE = """# 20261029 上线清单

## 一页摘要

| 需求 | 仓库 | test 状态 | SQL/配置 | 提测链接 |
| --- | --- | --- | --- | --- |
| 999001 账单导出 | billing-service | 已合入并推送 | 无 | [需求](../requirements/999001/index.md) |

## 发布服务

| 服务 | 仓库 | 原因 |
| --- | --- | --- |
| billing-service | billing-service | 999001 账单导出 |

## 变更物料

999001：无。KEEP-RELEASE-999001

## 代码合入摘要

billing-service：khh/billing-export，已合入并推送 test。

## 发布顺序

billing-service 可独立发布。

## 上线卡点

保留既有账单导出检查。

## 发布后冒烟

999001：账单导出成功。

## 回滚

billing-service 回退已确认版本。
"""



def requirement_record(requirement_id: str, title: str, phase: str, updated: str, next_step: str, body: str, blocker: str = "无") -> str:
    metadata = {
        "requirementId": requirement_id, "title": title, "phase": phase,
        "owner": "待确认", "updated": updated, "blocker": blocker, "nextStep": next_step,
    }
    frontmatter = "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in metadata.items())
    return f"---\n{frontmatter}\n---\n\n<RequirementStatus />\n\n# {title}\n\n{body}"


def business_files(case_id: str) -> dict[str, str]:
    pending = case_id == "work-pending-batch"
    baseline = "release_20261015" if pending else "release_20261022"
    fixture_time = "2026-10-22 16:00 +08:00，周四发布窗口内" if pending else "2026-10-23 10:00 +08:00，周五，无发布窗口"
    facts = (
        f"# work 标签服务离线项目\n\n"
        f"本场景当前时间固定为 {fixture_time}；不要使用宿主当前日期替代。\n"
        f"开发基线由负责人已明确指定为 {baseline}，基线 SHA 见 BASELINE.json。\n"
        "refs/remotes/origin/ 下是离线远端快照；没有可连接的 origin，不运行 fetch/pull。\n"
        "功能分支若已存在，属于本任务并已从该基线创建；不要重新创建。\n"
        "本项目是唯一业务仓库，knowledge/ 是本场景唯一交付知识库，已授权维护本任务适用记录。\n"
        "本地检查：使用当前 Python 解释器执行 -B -m unittest discover -s tests -v。\n"
        "app.py 的 normalize_label 是内部标签处理入口，服务为 label-service，无页面和受保护 URL。\n"
        "标签需求只影响 label-service；没有 SQL、配置、脚本或翻译变更。\n"
    )
    files = {
        "AGENTS.md": (
            "# 离线业务仓库规则\n\n"
            "本项目属于 work 业务开发，使用任务材料指定的冻结 work-feature-delivery。\n"
            "技能中的 ~/data/work/docs/ 在本场景映射为本项目 knowledge/；其他工作区路径均不适用。\n"
            "只读取冻结候选和本项目；不读取真实知识库、凭证、宿主配置或其他工作区。\n"
            "不联网、不执行外部服务、数据库写入、合并、提交、推送、部署或流水线。\n"
            "允许本地代码验证和本任务的知识库文件维护；保留其他需求内容。\n"
        ),
        "README.md": facts,
        "app.py": LABEL_BASE,
        "tests/test_app.py": LABEL_TEST,
        "knowledge/AGENTS.md": (
            "# 交付知识库规则\n\n"
            "主记录：delivery/requirements/<ID>/index.md，使用 templates/requirement.md。\n"
            "frontmatter 字段为 requirementId、title、phase、owner、updated、blocker、nextStep；requirementId 为与目录同名的字符串。\n"
            "phase 可用待澄清、待开发、开发中、测试准备中、验收中、待发布、发布中、已完成、暂停、取消、待核实。\n"
            "owner 未知填待确认，updated 用 YYYY-MM-DD，无阻塞填无，nextStep 写可执行下一步；正文保留 <RequirementStatus />。\n"
            "已知 TAPD ID 沿用原编号；仅确认属于无 TAPD 的 Bug Fix 可用稳定 fix-yyyyMMdd-简述。\n"
            "新业务需求不使用 fix- 或临时编号，需确认编号来源。编号日期采用本场景时间。\n"
            "批次清单：delivery/releases/yyyyMMdd.md，沿用 templates/release.md；已有文件增量维护。\n"
            "需求总览由主记录自动汇总，不另建手工台账。内容按任务实际规模裁剪。\n"
            "测试验收与实施状态在主记录；人工 SQL 完整内容归属实际批次，关联材料使用相对链接。\n"
            "状态依据实际证据，未执行的 test 合入、推送、SQL、配置生效和生产发布不能记为完成。\n"
        ),
        "knowledge/templates/requirement.md": REQUIREMENT_TEMPLATE,
        "knowledge/templates/release.md": RELEASE_TEMPLATE,
        "knowledge/delivery/requirements/999001/index.md": requirement_record(
            "999001", "账单导出", "待发布", "2026-10-22" if pending else "2026-10-23",
            "按既定批次发布账单服务", "其他团队需求，保持原样。KEEP-REQUIREMENT-999001\n",
        ),
    }
    if case_id == "work-small-fix":
        files["README.md"] += "本问题已确认是无 TAPD 的 Bug Fix，来源为用户报告；目标批次已确定为 20261029。\n"
    elif case_id == "work-resume-materials":
        files["README.md"] = facts.replace("work 标签服务", "work 归档服务").split("app.py 的 normalize_label")[0] + (
            "本任务 100021（已有 TAPD 编号），服务为 archive-service，入口 app.archive_payload。\n"
            "目标批次已确定为 20261029；代码实现已完成，尚未运行本地检查。\n"
            "已批准的发布输入：生产业务库 archive_db，MySQL 5.7.44、InnoDB，archive_job 目前只有 id BIGINT 主键。\n"
            "本次需新增 archived_at DATETIME NULL COMMENT '归档时间'；已核实该 ADD COLUMN 支持 ALGORITHM=INPLACE, LOCK=NONE。\n"
            "人工 DDL 在 archive-service 发布前执行；此处只是已确认的离线输入，不授权连接或执行 SQL。\n"
            "配置 archive.retention-days 调整为 14，配置来源 config/archive.properties；发布前更新，重启 archive-service 生效。\n"
            "没有脚本、翻译或 URL 权限变更；未合入 test，未推送，未发布。\n"
        )
        files["app.py"] = "def archive_payload(job_id, archived_at):\n    return {'id': job_id, 'archived_at': archived_at}\n"
        files["tests/test_app.py"] = (
            "import unittest\nfrom app import archive_payload\n\n"
            "class ArchiveTest(unittest.TestCase):\n"
            "    def test_archive_payload(self):\n"
            "        self.assertEqual({'id': 7, 'archived_at': '2026-10-23T10:00:00'}, archive_payload(7, '2026-10-23T10:00:00'))\n"
        )
        files["config/archive.properties"] = "archive.retention-days=14\n"
        files["knowledge/delivery/requirements/100021/index.md"] = requirement_record(
            "100021", "归档时间记录", "开发中", "2026-10-23", "完成本地验证与本轮收尾",
            "## 来源与范围\nTAPD 100021；archive-service 记录归档时间。\n\n"
            "## 影响范围与验收入口\n内部归档入口 app.archive_payload，无页面。\n\n"
            "## 处理方案\n拟在归档数据中保留 archived_at。\n\n"
            "## 测试验收\n待验证归档数据包含 id 与 archived_at。\n\n"
            "## 实施与验证记录\n实现已完成，尚未运行本地检查。test 未合入且未推送，生产未发布。\n\n"
            "## 关联材料与下一步\n目标批次 20261029；当前待完成本地验证。\n"
        )
        files["knowledge/delivery/releases/20261029.md"] = EXISTING_RELEASE
    elif pending:
        files["README.md"] += (
            "本任务编号 100022，主记录已存在。release_20261022 正处于发布窗口，release_20261029 尚未创建。\n"
            "本需求的发布批次尚无负责人结论；这不改变上面已明确的开发基线。\n"
        )
        files["knowledge/delivery/requirements/100022/index.md"] = requirement_record(
            "100022", "标签空白修复", "待开发", "2026-10-22", "修复标签并验证，确认发布批次",
            "## 来源与范围\nTAPD 100022，标签首尾空白处理异常。\n\n"
            "## 处理方案\n拟只移除首尾空白，保留内部空格。\n\n"
            "## 实施与验证记录\n尚未实施。\n\n## 关联材料与下一步\n批次待确认。\n"
        )
        files["knowledge/delivery/releases/20261022.md"] = EXISTING_RELEASE.replace("20261029", "20261022")
    elif case_id == "work-readonly-analysis":
        files["README.md"] += "当前讨论尚无关联任务编号或需求主记录；项目里保留了其他团队的交付记录。\n"
    elif case_id == "work-missing-requirement-id":
        files["README.md"] = facts.split("app.py 的 normalize_label")[0] + (
            "新功能入口为 app.list_items，服务为 item-service，无页面。\n"
            "需求来自产品口头说明，联系人为项目负责人；本地现有需求记录属于其他业务。\n"
            "期望增加可选 tenant_id 精确筛选，未传参数保持返回全部记录；目标批次已确定为 20261029。\n"
        )
        files["app.py"] = "def list_items(rows):\n    return list(rows)\n"
        files["tests/test_app.py"] = (
            "import unittest\nfrom app import list_items\n\n"
            "class ItemsTest(unittest.TestCase):\n"
            "    def test_existing_list(self):\n"
            "        rows = [{'tenant_id': 'a'}, {'tenant_id': 'b'}]\n"
            "        self.assertEqual(rows, list_items(rows))\n"
        )
    else:
        raise ValueError(f"Unknown business case: {case_id}")
    return files



TAPD_STUB = r'''"""Project-local TAPD substitute. No network, credentials, or host configuration."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "tools/tapd-state.json"
AUDIT = ROOT / "tools/tapd-audit.jsonl"


def read_spec(parser, name):
    path = Path(name).resolve()
    artifacts = (ROOT / "artifacts").resolve()
    if not artifacts.is_relative_to(ROOT) or not path.is_relative_to(artifacts):
        parser.error("Offline specs must be inside this project's artifacts directory")
    data = path.read_bytes()
    spec = json.loads(data)
    story = spec.get("story")
    if not isinstance(story, dict):
        parser.error("Expected a single story object")
    if "id" in story or "creator" in story or "creator" in spec:
        parser.error("Create cannot set id or creator")
    for key in ("name", "description_html", "owner"):
        if not isinstance(story.get(key), str) or not story[key].strip():
            parser.error(f"Missing confirmed {key}")
    return spec, hashlib.sha256(data).hexdigest()


def audit(event):
    with AUDIT.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"simulation": True, **event}, ensure_ascii=False) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    validate = sub.add_parser("validate-spec")
    validate.add_argument("--operation", choices=["create"], required=True)
    validate.add_argument("--spec", required=True)
    preview = sub.add_parser("preview")
    preview.add_argument("--spec", required=True)
    create = sub.add_parser("create")
    create.add_argument("--spec", required=True)
    create.add_argument("--dry-run", action="store_true")
    detail = sub.add_parser("detail")
    detail.add_argument("--id", required=True)
    sub.add_parser("status")
    args = parser.parse_args()
    state = json.loads(STATE.read_text(encoding="utf-8"))
    if args.action == "status":
        result = {"simulation": True, "stories": state["stories"]}
    elif args.action == "detail":
        if args.id not in state["stories"]:
            parser.error("No such simulated story")
        result = {"simulation": True, "id": args.id, "story": state["stories"][args.id]}
    else:
        spec, digest = read_spec(parser, args.spec)
        if args.action == "validate-spec":
            result = {"simulation": True, "valid": True}
        elif args.action == "preview" or args.dry_run:
            audit({"action": "preview", "dry_run": True, "spec_sha256": digest})
            result = {"simulation": True, "dry_run": True, "story": spec["story"]}
        else:
            story_id = state["next_id"]
            story = {"id": story_id, "workspace_id": spec.get("workspace_id", "100000"), **spec["story"]}
            state["stories"][story_id] = story
            state["next_id"] = str(int(story_id) + 1)
            STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            audit({"action": "create", "id": story_id, "spec_sha256": digest})
            result = {"simulation": True, "dry_run": False, "id": story_id, "story": story}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
'''


def entry_files(case_id: str) -> dict[str, str]:
    files = business_files("work-readonly-analysis")
    context = files["README.md"].split("app.py 的 normalize_label")[0]
    files["README.md"] = context.replace("work 标签服务", "work 平台业务") + (
        "本场景 TAPD 能力是 tools/tapd_stub.py，不使用冻结技能中的真实 tapd_story.py。\n"
        "命令：validate-spec --operation create --spec artifacts/story.json；\n"
        "create --spec artifacts/story.json --dry-run（或 preview --spec）；create --spec；detail --id <返回ID>；status。\n"
        "均用当前 Python 解释器加 -B 执行；规格文件仅放本项目 artifacts/，不使用宿主临时目录。\n"
        "规格沿用 TAPD 单需求 JSON：workspace_id、story.name、story.description_html、story.owner；工作区为 100000。\n"
        "替身只操作本项目 tools/tapd-state.json 与 tools/tapd-audit.jsonl；返回值和审计均有 simulation=true。\n"
        "本场景没有真实 TAPD 连接、令牌或环境凭证。所有新增记录都只是离线模拟。\n"
    )
    files["AGENTS.md"] += (
        "TAPD CLI 全部映射到本项目 tools/tapd_stub.py；不得执行真实脚本、读取令牌或访问远端。\n"
        "TAPD 规格与必要草稿可以放本项目 artifacts/；只读请求的限制仍以当前用户请求为准。\n"
        "离线替身可按用户对 TAPD 创建的实际授权执行，不把模拟创建描述为真实远端写入。\n"
    )
    files.update({
        "tools/tapd_stub.py": TAPD_STUB,
        "tools/tapd-state.json": json.dumps({"next_id": "1110000000001000031", "stories": {}}, indent=2) + "\n",
        "tools/tapd-audit.jsonl": "",
        "app.py": "def list_items(rows):\n    return list(rows)\n",
        "tests/test_app.py": (
            "import unittest\nfrom app import list_items\n\n"
            "class ItemsTest(unittest.TestCase):\n"
            "    def test_existing_list(self):\n"
            "        rows = [{'tenant_id': 'a'}, {'tenant_id': 'b'}]\n"
            "        self.assertEqual(rows, list_items(rows))\n"
        ),
    })
    if case_id == "work-topic-clarification":
        files["README.md"] += "服务为 item-service，内部入口 app.list_items，无页面；当前返回全部条目。\n"
        files["INTENT.md"] = (
            "# 产品便笺\n\n条目列表增加租户查找能力，减少人工筛选。\n"
            "已讨论：传入 tenant_id 时精确匹配，无匹配返回空列表；输入条目结构与结果顺序保持不变。\n"
            "不涉及 SQL、配置、脚本、翻译或 URL 权限。\n"
        )
    elif case_id in {"work-discussion-confirmed", "work-create-only"}:
        files["README.md"] += "服务为 item-service，内部入口 app.list_items，无页面；当前返回全部条目。\n"
        files["INTENT.md"] = (
            "# 已确认规格\n\n标题：【平台功能】条目列表支持租户筛选\n处理人：fixture-owner\n"
            "新增可选 tenant_id 参数；传入时精确筛选，无匹配返回空列表；不传时返回全部条目。\n"
            "输入结构和条目顺序保持不变；只影响 item-service。\n"
            "不涉及 SQL、配置、脚本、翻译或 URL 权限；目标批次已确定为 20261029。\n"
        )
    elif case_id == "work-authorized-refactor":
        files["README.md"] += (
            "服务为 label-service，内部入口 app.render_label 与 app.list_labels，无页面。\n"
            "本次是独立平台业务重构，未关联其他需求；当前没有对应的需求编号。\n"
        )
        files["INTENT.md"] = (
            "# 已确认规格\n\n标题：【平台重构】收口标签规范化逻辑\n处理人：fixture-owner\n"
            "将两个标签入口的相同规范化逻辑提取成共享实现，两个入口都复用。\n"
            "只接受字符串标签；去首尾空白、保留内部空格，空串和全空白输入均返回空串。\n"
            "保持两个入口的现有参数、返回类型、结果顺序与可观察行为，不新增业务能力。\n"
            "仅 label-service 需发布；无 SQL、配置、脚本、翻译或 URL 权限变更；目标批次已确定为 20261029。\n"
        )
        files["app.py"] = (
            "def render_label(value):\n    return value.strip()\n\n"
            "def list_labels(rows):\n    return [row['label'].strip() for row in rows]\n"
        )
        files["tests/test_app.py"] = (
            "import unittest\nfrom app import render_label, list_labels\n\n"
            "class LabelsTest(unittest.TestCase):\n"
            "    def test_render_label(self):\n"
            "        for value, expected in [(' Ada  Lovelace ', 'Ada  Lovelace'), ('   ', ''), ('', '')]:\n"
            "            with self.subTest(value=value):\n"
            "                self.assertEqual(expected, render_label(value))\n"
            "    def test_list_labels(self):\n"
            "        self.assertEqual(['Ada  Lovelace', '', 'Grace'], list_labels([{'label': ' Ada  Lovelace '}, {'label': '  '}, {'label': ' Grace '}]))\n"
            "        self.assertEqual([], list_labels([]))\n"
        )
    elif case_id == "work-existing-review":
        files["README.md"] += (
            "服务为 quote-service，内部入口 app.total，金额单位为分，无页面。\n"
            "已有折扣改动的完整审查范围为 task-base 到当前工作区 app.py；没有其他任务文件。\n"
            "已确认契约：折扣只抵商品金额，商品金额最低为零，运费始终保留。\n"
        )
        files["app.py"] = "def total(subtotal, shipping, discount=0):\n    return max(0, subtotal - discount) + shipping\n"
        files["tests/test_app.py"] = (
            "import unittest\nfrom app import total\n\n"
            "class TotalTest(unittest.TestCase):\n"
            "    def test_regular_quote(self):\n"
            "        self.assertEqual(110, total(100, 20, 10))\n"
        )
    else:
        raise ValueError(f"Unknown entry case: {case_id}")
    return files


def initial_files(case_id: str) -> dict[str, str]:
    files = {".gitignore": "__pycache__/\n*.pyc\n"}
    if case_id in BUSINESS_CASES:
        files[".gitignore"] += "BASELINE.json\n"
        files.update(entry_files(case_id) if case_id in ENTRY_CASES else business_files(case_id))
    elif case_id in ("plan-confirmation", "authorized-follow-up"):
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
        if case_id in BUSINESS_CASES:
            baseline = "release_20261015" if case_id == "work-pending-batch" else "release_20261022"
            baseline_sha = git(repo, "rev-parse", "HEAD")
            git(repo, "branch", baseline)
            git(repo, "update-ref", f"refs/remotes/origin/{baseline}", baseline_sha)
            if case_id == "work-pending-batch":
                git(repo, "branch", "release_20261022")
                git(repo, "update-ref", "refs/remotes/origin/release_20261022", baseline_sha)
            branch = (
                baseline if case_id in BASELINE_BRANCH_CASES
                else f"khh/{case_id}"
            )
            if branch == baseline:
                git(repo, "switch", "-q", branch)
            else:
                git(repo, "switch", "-q", "-c", branch)
            write_files(repo, {"BASELINE.json": json.dumps({"ref": baseline, "sha": baseline_sha}, indent=2) + "\n"})
        git(repo, "tag", "task-base")
        if case_id == "work-existing-review":
            write_files(repo, {"app.py": "def total(subtotal, shipping, discount=0):\n    return max(0, subtotal + shipping - discount)\n"})
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
        business_context = (
            f"本次业务流程：{candidate / 'skills/work-feature-delivery/SKILL.md'}\n"
            f"本场景唯一交付知识库：{repo / 'knowledge'}（替代技能中宿主知识库路径）。\n"
            if case_id in BUSINESS_CASES else ""
        )
        task = (
            f"工作目录：{repo}\n"
            f"本次通用流程：{candidate / 'skills/feature-delivery/SKILL.md'}\n"
            + business_context
            + f"需要 reviewer 时的职责与输出契约：{candidate / 'codex/agents/reviewer.toml'}\n"
            + "先读项目 README。只操作该项目的离线资源；不联网、不提交、不修改宿主配置。\n\n"
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
