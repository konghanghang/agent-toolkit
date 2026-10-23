#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""拉取 TAPD 需求并生成工作周报。"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

COMMON_DIR = Path(__file__).resolve().parents[3] / "common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from work_config import WorkConfigError, load_work_config

DEFAULT_BASE_URL = "https://api.tapd.cn"


def load_report_config(config_path=None, workspace_id=None):
    config = load_work_config(config_path)
    section = config.get("tapd_weekly_report")
    if not isinstance(section, dict):
        raise WorkConfigError("工作配置缺少 tapd_weekly_report 对象。")

    raw_members = section.get("members")
    if not isinstance(raw_members, list) or not raw_members or any(
        not isinstance(member, str) or not member.strip() for member in raw_members
    ):
        raise WorkConfigError("tapd_weekly_report.members 必须为非空成员名称数组。")
    members = [member.strip() for member in raw_members]
    if any(";" in member for member in members):
        raise WorkConfigError("tapd_weekly_report.members 的成员名称不能包含分隔符 ';'。")
    if len(set(members)) != len(members):
        raise WorkConfigError("tapd_weekly_report.members 不能包含重复成员。")

    if workspace_id is None:
        workspace_id = os.getenv("TAPD_WORKSPACE_ID", "").strip() or section.get("workspace_id")
    if not isinstance(workspace_id, str) or not workspace_id.strip():
        raise WorkConfigError(
            "缺少工作区 ID：请配置 tapd_weekly_report.workspace_id，"
            "或使用 --workspace-id / TAPD_WORKSPACE_ID。"
        )
    return workspace_id.strip(), members


def parse_owners(owner_str, members=None):
    if not owner_str:
        return []
    if not members:
        return owner_str.replace(";", " ").split()
    names = "|".join(re.escape(member) for member in sorted(members, key=len, reverse=True))
    return [
        owner
        for segment in owner_str.split(";")
        for owner in re.findall(rf"(?<!\S)(?:{names})(?!\S)|\S+", segment)
    ]


def classify_story(owners, members):
    return next((member for member in members if member in owners), None)


def clean_story_name(name):
    return re.sub(r"【[^】]+】", "", name).strip()


def fetch_stories(version, token, workspace_id, base_url):
    params = {
        "workspace_id": workspace_id,
        "version": version,
        "limit": 100,
        "fields": "id,name,owner,status,version",
    }
    url = f"{base_url.rstrip('/')}/stories?{urlencode(params)}"
    request = Request(url, headers={"Authorization": f"Bearer {token}"})
    with urlopen(request, timeout=30) as response:
        payload = json.load(response)
    if payload.get("status") != 1:
        raise RuntimeError(f"TAPD API 返回错误: {payload.get('info')}")
    return payload.get("data", [])


def process_stories(stories_data, members):
    result = {name: [] for name in members}

    for item in stories_data:
        story = item.get("Story", {})
        story_name = story.get("name", "")
        owner_str = story.get("owner", "")
        status = story.get("status", "")

        if status == "rejected":
            continue

        owners = parse_owners(owner_str, members)
        assigned_to = classify_story(owners, members)
        if assigned_to:
            result[assigned_to].append(
                {
                    "name": clean_story_name(story_name),
                    "original_name": story_name,
                    "owners": owners,
                    "status": status,
                }
            )

    return result


def format_member_counts(counts, members, separator="，"):
    return separator.join(f"{member}: {counts[member]}" for member in members)


def generate_weekly_report(completed_stories, planned_stories, week_start_date, members):
    start = datetime.strptime(week_start_date, "%Y-%m-%d")
    end = start + timedelta(days=6)
    date_range = f"{start.strftime('%Y-%m-%d')} ~ {end.strftime('%Y-%m-%d')}"

    completed_counts = {name: len(completed_stories[name]) for name in members}
    planned_counts = {name: len(planned_stories[name]) for name in members}
    total_completed = sum(completed_counts.values())
    total_planned = sum(planned_counts.values())

    report = f"# 工作周报 ({date_range})\n\n"
    report += "## 本周完成\n\n"
    for member in members:
        if completed_stories[member]:
            report += f"### {member}\n"
            for story in completed_stories[member]:
                report += f"- {story['name']}\n"
            report += "\n"

    report += f"**本周完成数量统计**：共 {total_completed} 个需求"
    report += f"（{format_member_counts(completed_counts, members)}）\n\n"

    report += "---\n\n"

    report += "## 下周计划\n\n"
    for member in members:
        if planned_stories[member]:
            report += f"### {member}\n"
            for story in planned_stories[member]:
                report += f"- {story['name']}\n"
            report += "\n"

    report += f"**下周计划数量统计**：共 {total_planned} 个需求"
    report += f"（{format_member_counts(planned_counts, members)}）\n"

    return report, completed_counts, planned_counts


def parse_version_date(version):
    try:
        return datetime.strptime(version, "%Y-%m%d")
    except ValueError as exc:
        raise ValueError("version 格式必须是 YYYY-MMDD，例如 2026-0115") from exc


def build_output_path(output_root, week_start_date, ensure_dir=True):
    year = week_start_date[:4]
    month = week_start_date[5:7]
    output_dir = Path(output_root) / year / month
    if ensure_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / f"weekly-report-{week_start_date}.md"


def main():
    parser = argparse.ArgumentParser(description="生成 TAPD 工作周报。")
    parser.add_argument("--version", required=True, help="当前版本，例如 2026-0115")
    parser.add_argument("--next-version", help="下一个版本，例如 2026-0122")
    parser.add_argument("--output-root", default="reports", help="周报输出根目录")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="TAPD API 基础地址")
    parser.add_argument("--config", help="本机工作配置文件路径")
    parser.add_argument("--workspace-id", help="覆盖环境变量和工作配置中的工作区 ID")
    parser.add_argument("--dry-run", action="store_true", help="只输出推导结果，不真正写文件")
    args = parser.parse_args()

    version_date = parse_version_date(args.version)
    next_version = args.next_version
    if not next_version:
        next_version = (version_date + timedelta(days=7)).strftime("%Y-%m%d")

    week_start = (version_date - timedelta(days=version_date.weekday())).strftime("%Y-%m-%d")
    output_path = build_output_path(args.output_root, week_start, ensure_dir=False)

    if args.dry_run:
        print(f"version={args.version}")
        print(f"next_version={next_version}")
        print(f"week_start={week_start}")
        print(f"output={output_path}")
        return 0

    token = os.getenv("TAPD_TOKEN")
    if not token:
        print("缺少 TAPD_TOKEN 环境变量。", file=sys.stderr)
        return 1

    try:
        workspace_id, members = load_report_config(args.config, args.workspace_id)
    except WorkConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    completed_data = fetch_stories(args.version, token, workspace_id, args.base_url)
    planned_data = fetch_stories(next_version, token, workspace_id, args.base_url)

    completed_stories = process_stories(completed_data, members)
    planned_stories = process_stories(planned_data, members)

    report, completed_counts, planned_counts = generate_weekly_report(
        completed_stories, planned_stories, week_start, members
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write(report)

    print(f"周报已生成：{output_path}")
    print("本周完成统计 - " + format_member_counts(completed_counts, members, ", "))
    print("下周计划统计 - " + format_member_counts(planned_counts, members, ", "))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
