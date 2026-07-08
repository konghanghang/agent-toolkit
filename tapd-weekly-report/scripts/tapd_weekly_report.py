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

TEAM_MEMBERS = ["余佳烨", "彭辉", "孔航航"]
DEFAULT_BASE_URL = "https://api.tapd.cn"
DEFAULT_WORKSPACE_ID = "35238004"


def parse_owners(owner_str):
    if not owner_str:
        return []
    return [o.strip() for o in owner_str.replace(";", " ").split() if o.strip()]


def classify_story(owners):
    team_owners = [o for o in owners if o in TEAM_MEMBERS]
    if not team_owners:
        return None

    if "孔航航" in team_owners:
        if len(team_owners) == 1:
            return "孔航航"
        other_members = [o for o in team_owners if o != "孔航航"]
        if len(other_members) == 1:
            return other_members[0]
        if "余佳烨" in other_members:
            return "余佳烨"
        if "彭辉" in other_members:
            return "彭辉"
    else:
        if "余佳烨" in team_owners:
            return "余佳烨"
        if "彭辉" in team_owners:
            return "彭辉"

    return None


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


def process_stories(stories_data):
    result = {name: [] for name in TEAM_MEMBERS}

    for item in stories_data:
        story = item.get("Story", {})
        story_name = story.get("name", "")
        owner_str = story.get("owner", "")
        status = story.get("status", "")

        if status == "rejected":
            continue

        owners = parse_owners(owner_str)
        assigned_to = classify_story(owners)
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


def generate_weekly_report(completed_stories, planned_stories, week_start_date):
    start = datetime.strptime(week_start_date, "%Y-%m-%d")
    end = start + timedelta(days=6)
    date_range = f"{start.strftime('%Y-%m-%d')} ~ {end.strftime('%Y-%m-%d')}"

    completed_counts = {name: len(stories) for name, stories in completed_stories.items()}
    planned_counts = {name: len(stories) for name, stories in planned_stories.items()}
    total_completed = sum(completed_counts.values())
    total_planned = sum(planned_counts.values())

    report = f"# 工作周报 ({date_range})\n\n"
    report += "## 本周完成\n\n"
    for member in TEAM_MEMBERS:
        if completed_stories[member]:
            report += f"### {member}\n"
            for story in completed_stories[member]:
                report += f"- {story['name']}\n"
            report += "\n"

    report += f"**本周完成数量统计**：共 {total_completed} 个需求"
    report += f"（余佳烨: {completed_counts['余佳烨']}，"
    report += f"彭辉: {completed_counts['彭辉']}，"
    report += f"孔航航: {completed_counts['孔航航']}）\n\n"

    report += "---\n\n"

    report += "## 下周计划\n\n"
    for member in TEAM_MEMBERS:
        if planned_stories[member]:
            report += f"### {member}\n"
            for story in planned_stories[member]:
                report += f"- {story['name']}\n"
            report += "\n"

    report += f"**下周计划数量统计**：共 {total_planned} 个需求"
    report += f"（余佳烨: {planned_counts['余佳烨']}，"
    report += f"彭辉: {planned_counts['彭辉']}，"
    report += f"孔航航: {planned_counts['孔航航']}）\n"

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
    parser.add_argument("--workspace-id", default=os.getenv("TAPD_WORKSPACE_ID", DEFAULT_WORKSPACE_ID))
    parser.add_argument("--dry-run", action="store_true", help="只输出推导结果，不真正写文件")
    args = parser.parse_args()

    version_date = parse_version_date(args.version)
    next_version = args.next_version
    if not next_version:
        next_version = (version_date + timedelta(days=7)).strftime("%Y-%m%d")

    week_start = (version_date - timedelta(days=version_date.weekday())).strftime("%Y-%m-%d")
    output_path = build_output_path(args.output_root, week_start, ensure_dir=not args.dry_run)

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

    completed_data = fetch_stories(args.version, token, args.workspace_id, args.base_url)
    planned_data = fetch_stories(next_version, token, args.workspace_id, args.base_url)

    completed_stories = process_stories(completed_data)
    planned_stories = process_stories(planned_data)

    report, completed_counts, planned_counts = generate_weekly_report(
        completed_stories, planned_stories, week_start
    )

    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write(report)

    print(f"周报已生成：{output_path}")
    print(
        "本周完成统计 - "
        f"余佳烨: {completed_counts['余佳烨']}, "
        f"彭辉: {completed_counts['彭辉']}, "
        f"孔航航: {completed_counts['孔航航']}"
    )
    print(
        "下周计划统计 - "
        f"余佳烨: {planned_counts['余佳烨']}, "
        f"彭辉: {planned_counts['彭辉']}, "
        f"孔航航: {planned_counts['孔航航']}"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
