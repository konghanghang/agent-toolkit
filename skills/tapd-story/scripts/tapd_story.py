#!/usr/bin/env python3
"""Read, inspect, create, and update TAPD stories with explicit modes."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from tapd_api import DEFAULT_BASE_URL, DEFAULT_FIELDS, TapdClient, TapdError, story_link
from tapd_content import build_story_view, validate_description_html
from tapd_repo_scan import scan_context


def _add_read_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--id", required=True, help="TAPD 需求 ID")
    parser.add_argument("--workspace-id", help="TAPD 工作区 ID；也可使用 TAPD_WORKSPACE_ID")
    parser.add_argument("--fields", default=DEFAULT_FIELDS, help="逗号分隔的 TAPD 字段")
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"TAPD API 地址，默认 {DEFAULT_BASE_URL}",
    )
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--output", help="可选输出文件")
    parser.add_argument("--download-images", action="store_true", help="通过 TAPD 图片接口下载正文图片")
    parser.add_argument("--image-dir", help="图片保存目录；默认使用系统临时目录")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="读取和管理 TAPD 需求。")
    subparsers = parser.add_subparsers(dest="command", required=True)

    detail = subparsers.add_parser("detail", help="读取需求详情")
    _add_read_arguments(detail)
    detail.add_argument("--raw", action="store_true", help="JSON 输出中包含原始 TAPD 响应")

    context = subparsers.add_parser("plan-context", help="读取需求并生成仓库实施上下文")
    _add_read_arguments(context)
    context.add_argument("--repo", default=".", help="当前仓库根目录")
    context.add_argument("--include-workspace", action="store_true", help="同时检索更大的工作区")
    context.add_argument("--workspace-root", help="工作区根目录；也可使用 WORK_ROOT")
    context.add_argument("--raw", action="store_true", help="JSON 输出中包含原始 TAPD 响应")

    validate = subparsers.add_parser("validate-spec", help="只校验创建或更新规格，不访问 TAPD")
    validate.add_argument("--operation", choices=("create", "update"), required=True)
    validate.add_argument("--spec", required=True, help="JSON 规格文件")
    validate.add_argument("--workspace-id", help="可选工作区 ID")

    for command, help_text in (("create", "创建 TAPD 需求"), ("update", "更新 TAPD 需求")):
        write = subparsers.add_parser(command, help=help_text)
        write.add_argument("--spec", required=True, help="JSON 规格文件")
        write.add_argument("--workspace-id", help="TAPD 工作区 ID")
        write.add_argument(
            "--base-url",
            default=DEFAULT_BASE_URL,
            help=f"TAPD API 地址，默认 {DEFAULT_BASE_URL}",
        )
        write.add_argument("--dry-run", action="store_true", help="只输出操作计划，不调用写接口")

    return parser.parse_args()


def _resolve_workspace_id(cli_value: Optional[str], spec: Optional[Dict[str, Any]] = None) -> str:
    candidates = [cli_value]
    if spec:
        candidates.append(spec.get("workspace_id"))
    candidates.append(os.getenv("TAPD_WORKSPACE_ID"))
    for value in candidates:
        text = str(value or "").strip()
        if text:
            return text
    raise TapdError("缺少 TAPD 工作区 ID，请传 --workspace-id、在规格中填写 workspace_id，或设置 TAPD_WORKSPACE_ID。")


def _write_output(content: str, output: Optional[str]) -> None:
    if not output:
        sys.stdout.write(content)
        return
    path = Path(output).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(path)


def _image_dir(args: argparse.Namespace, story_id: str) -> Path:
    if args.image_dir:
        return Path(args.image_dir).expanduser().resolve()
    return Path(tempfile.gettempdir()) / f"tapd-story-{story_id}" / "images"


def _download_images(client: TapdClient, story_view: Dict[str, Any], output_dir: Path) -> None:
    for image in story_view.get("images", []):
        try:
            image.update(
                client.download_image(
                    story_view["workspace_id"],
                    image["src"],
                    output_dir,
                    int(image["index"]),
                )
            )
        except TapdError as exc:
            image["download_error"] = str(exc)


def _render_detail_markdown(story: Dict[str, Any]) -> str:
    lines = [
        f"# TAPD 需求详情 {story['story_id']}",
        "",
        "## 基本信息",
        f"- 标题：{story['title']}",
        f"- 需求 ID：{story['story_id']}",
        f"- 工作区：{story['workspace_id']}",
    ]
    for label, key in (
        ("状态", "status"),
        ("优先级", "priority"),
        ("负责人", "owner"),
        ("创建人", "creator"),
        ("开发", "developer"),
        ("版本", "version"),
        ("迭代 ID", "iteration_id"),
        ("创建时间", "created"),
        ("更新时间", "modified"),
    ):
        if story.get(key):
            lines.append(f"- {label}：{story[key]}")

    lines.extend(["", "## 需求原文", story["detail"]])
    if story.get("urls"):
        lines.extend(["", "## 外部链接"])
        lines.extend(f"- {url}" for url in story["urls"])
    if story.get("field_codes"):
        lines.extend(["", "## 字段编码", f"- {', '.join(story['field_codes'])}"])
    if story.get("images"):
        lines.extend(["", "## 图片资源"])
        for image in story["images"]:
            lines.append(f"- 图片 {image['index']}：{image['src']}")
            if image.get("context"):
                lines.append(f"  - 正文上下文：{image['context']}")
            if image.get("local_path"):
                lines.append(f"  - 本地文件：{image['local_path']}")
                lines.append("  - 状态：待视觉/OCR 读取；清晰内容可纳入需求事实")
            if image.get("download_error"):
                lines.append(f"  - 下载失败：{image['download_error']}")
    return "\n".join(lines).strip() + "\n"


def _render_hits(hits: Optional[Iterable[Dict[str, Any]]]) -> List[str]:
    if not hits:
        return ["- 未命中。"]
    return [f"- {hit['path']}:{hit['line']} [{hit['category']}] {hit['snippet']}" for hit in hits]


def _render_plan_context_markdown(story: Dict[str, Any], context: Dict[str, Any]) -> str:
    lines = [
        f"# TAPD 实施上下文 {story['story_id']}",
        "",
        "## 已确认需求事实",
        f"- 标题：{story['title']}",
        f"- 需求 ID：{story['story_id']}",
        f"- 工作区：{story['workspace_id']}",
    ]
    if story.get("owner"):
        lines.append(f"- 负责人：{story['owner']}")
    if story.get("version"):
        lines.append(f"- 版本：{story['version']}")
    if story.get("priority"):
        lines.append(f"- 优先级：{story['priority']}")

    lines.extend(["", "## 检索线索", f"- 检索词：{', '.join(context['search_terms'])}"])
    if story.get("field_codes"):
        lines.append(f"- 字段编码：{', '.join(story['field_codes'])}")
    if story.get("urls"):
        lines.extend(f"- 外部链接：{url}" for url in story["urls"])

    if story.get("images"):
        lines.extend(["", "## 图片上下文"])
        for image in story["images"]:
            lines.append(f"- 图片 {image['index']}：{image['src']}")
            if image.get("context"):
                lines.append(f"  - 正文上下文：{image['context']}")
            if image.get("local_path"):
                lines.append(f"  - 本地文件：{image['local_path']}")
                lines.append("  - 后续动作：执行视觉/OCR 读取；仅将不清晰或有歧义的部分标记为待确认")
            if image.get("download_error"):
                lines.append(f"  - 下载失败：{image['download_error']}")

    lines.extend(["", f"## 当前仓库命中（{context['repo_root']}）"])
    lines.extend(_render_hits(context.get("repo_hits")))
    lines.extend(["", "## 当前仓库候选文件"])
    candidates = context.get("repo_candidates") or []
    if not candidates:
        lines.append("- 暂无候选文件。")
    for item in candidates:
        lines.append(
            f"- {item['path']} [{item['category']}] "
            f"命中 {item['hit_count']} 次；关键词：{', '.join(item['terms'])}"
        )

    if context.get("workspace_hits") is not None:
        lines.extend(["", f"## 工作区候选项目（{context['workspace_root']}）"])
        projects = context.get("workspace_projects") or []
        if not projects:
            lines.append("- 暂无候选项目。")
        for item in projects:
            categories = ", ".join(f"{key}={value}" for key, value in item["categories"].items())
            lines.append(
                f"- {item['project']}：命中 {item['hit_count']} 次；"
                f"分类 {categories or 'other'}；关键词：{', '.join(item['terms'])}"
            )
        lines.extend(["", "## 工作区命中"])
        lines.extend(_render_hits(context.get("workspace_hits")))

    lines.extend(
        [
            "",
            "## 需求原文",
            story["detail"],
            "",
            "## 后续计划要求",
            "- 读取当前目录适用的 AGENTS.md。",
            "- 结合代码证据区分确认事实、推断路径和待确认项。",
            "- 不根据脚本命中结果自动判定前端或后端为主落点。",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def _load_spec(path: str) -> Dict[str, Any]:
    try:
        data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except OSError as exc:
        raise TapdError(f"无法读取规格文件 {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise TapdError(f"规格文件不是有效 JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise TapdError("规格文件顶层必须是 JSON 对象。")
    return data


def _validate_common_item(item: Any, label: str) -> List[str]:
    errors = []
    if not isinstance(item, dict):
        return [f"{label} 必须是对象"]
    if "creator" in item:
        errors.append(f"{label} 禁止传 creator")
    if "owner" in item and not isinstance(item["owner"], str):
        errors.append(f"{label}.owner 必须是字符串")
    if "description_html" in item:
        errors.extend(f"{label}.{message}" for message in validate_description_html(item["description_html"]))
    return errors


def _validate_create_spec(spec: Dict[str, Any]) -> tuple[List[str], List[Dict[str, Any]]]:
    errors = []
    allowed_top = {"workspace_id", "story", "parent", "children"}
    unknown = sorted(set(spec) - allowed_top)
    if unknown:
        errors.append(f"create 规格包含未知字段: {', '.join(unknown)}")

    has_story = "story" in spec
    has_parent = "parent" in spec
    if has_story == has_parent:
        errors.append("create 规格必须且只能包含 story 或 parent 其中一种")
        return errors, []

    operations: List[Dict[str, Any]] = []
    if has_story:
        if spec.get("children"):
            errors.append("单需求 story 模式不能包含 children")
        items = [("story", spec.get("story"))]
    else:
        children = spec.get("children")
        if not isinstance(children, list) or not children:
            errors.append("parent 模式必须包含非空 children 数组")
            children = []
        items = [("parent", spec.get("parent"))] + [
            (f"child[{index}]", child) for index, child in enumerate(children, start=1)
        ]

    for label, item in items:
        item_errors = _validate_common_item(item, label)
        if isinstance(item, dict):
            if "id" in item:
                item_errors.append(f"{label} 是创建项，禁止包含 id")
            if not str(item.get("name") or "").strip():
                item_errors.append(f"{label}.name 不能为空")
            if "description_html" not in item:
                item_errors.append(f"{label}.description_html 不能为空")
            operations.append(
                {
                    "role": label,
                    "name": item.get("name", ""),
                    "owner": item.get("owner") or "",
                    "fields": [key for key in ("name", "description_html", "owner") if key in item],
                }
            )
        errors.extend(item_errors)
    return errors, operations


def _validate_update_spec(spec: Dict[str, Any]) -> tuple[List[str], List[Dict[str, Any]]]:
    errors = []
    allowed_top = {"workspace_id", "stories"}
    unknown = sorted(set(spec) - allowed_top)
    if unknown:
        errors.append(f"update 规格包含未知字段: {', '.join(unknown)}")

    stories = spec.get("stories")
    if not isinstance(stories, list) or not stories:
        return errors + ["update 规格必须包含非空 stories 数组"], []

    operations = []
    allowed_fields = {"id", "name", "description_html", "owner"}
    for index, item in enumerate(stories, start=1):
        label = f"stories[{index}]"
        item_errors = _validate_common_item(item, label)
        if isinstance(item, dict):
            unknown_item = sorted(set(item) - allowed_fields)
            if unknown_item:
                item_errors.append(f"{label} 包含未知字段: {', '.join(unknown_item)}")
            if not str(item.get("id") or "").strip():
                item_errors.append(f"{label}.id 不能为空")
            changed_fields = [key for key in ("name", "description_html", "owner") if key in item]
            if not changed_fields:
                item_errors.append(f"{label} 至少要包含一个更新字段")
            if "name" in item and not str(item.get("name") or "").strip():
                item_errors.append(f"{label}.name 不能为空字符串")
            operations.append(
                {
                    "role": label,
                    "id": str(item.get("id") or ""),
                    "name": item.get("name", ""),
                    "owner": item.get("owner") if "owner" in item else "未修改",
                    "fields": changed_fields,
                }
            )
        errors.extend(item_errors)
    return errors, operations


def _validate_spec(operation: str, spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    errors, operations = (
        _validate_create_spec(spec) if operation == "create" else _validate_update_spec(spec)
    )
    if errors:
        raise TapdError("规格校验失败:\n- " + "\n- ".join(errors))
    return operations


def _story_result(role: str, workspace_id: str, story: Dict[str, Any]) -> Dict[str, Any]:
    story_id = str(story.get("id") or "")
    return {
        "role": role,
        "id": story_id,
        "name": story.get("name", ""),
        "parent_id": str(story.get("parent_id") or "0"),
        "link": story_link(workspace_id, story_id),
        "modified": story.get("modified", ""),
    }


def _print_json(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def command_detail(args: argparse.Namespace) -> int:
    workspace_id = _resolve_workspace_id(args.workspace_id)
    client = TapdClient(args.base_url)
    raw_payload, raw_story = client.get_story(workspace_id, args.id, args.fields)
    story = build_story_view(args.id, workspace_id, raw_story)
    if args.download_images and story["images"]:
        _download_images(client, story, _image_dir(args, args.id))

    if args.format == "json":
        payload: Dict[str, Any] = {"story": story}
        if args.raw:
            payload["raw"] = raw_payload
        rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    else:
        rendered = _render_detail_markdown(story)
    _write_output(rendered, args.output)
    return 0


def command_plan_context(args: argparse.Namespace) -> int:
    workspace_id = _resolve_workspace_id(args.workspace_id)
    repo_root = Path(args.repo).expanduser().resolve()
    if not repo_root.exists():
        raise TapdError(f"仓库目录不存在: {repo_root}")

    workspace_root_value = args.workspace_root or os.getenv("WORK_ROOT")
    workspace_root = Path(workspace_root_value).expanduser().resolve() if workspace_root_value else None
    if args.include_workspace and (workspace_root is None or not workspace_root.exists()):
        raise TapdError("使用 --include-workspace 时必须提供有效的 --workspace-root 或 WORK_ROOT。")

    client = TapdClient(args.base_url)
    raw_payload, raw_story = client.get_story(workspace_id, args.id, args.fields)
    story = build_story_view(args.id, workspace_id, raw_story)
    if args.download_images and story["images"]:
        _download_images(client, story, _image_dir(args, args.id))
    context = scan_context(
        story,
        repo_root,
        include_workspace=args.include_workspace,
        workspace_root=workspace_root,
    )

    if args.format == "json":
        payload: Dict[str, Any] = {"story": story, "context": context}
        if args.raw:
            payload["raw"] = raw_payload
        rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    else:
        rendered = _render_plan_context_markdown(story, context)
    _write_output(rendered, args.output)
    return 0


def command_validate_spec(args: argparse.Namespace) -> int:
    spec = _load_spec(args.spec)
    operations = _validate_spec(args.operation, spec)
    workspace_id = args.workspace_id or spec.get("workspace_id") or os.getenv("TAPD_WORKSPACE_ID")
    _print_json(
        {
            "valid": True,
            "operation": args.operation,
            "workspace_id": str(workspace_id or "未设置"),
            "operations": operations,
        }
    )
    return 0


def command_create(args: argparse.Namespace) -> int:
    spec = _load_spec(args.spec)
    operations = _validate_spec("create", spec)
    workspace_id = _resolve_workspace_id(args.workspace_id, spec)
    if args.dry_run:
        _print_json(
            {
                "status": "dry_run",
                "mode": "create",
                "workspace_id": workspace_id,
                "operations": operations,
                "writes_performed": False,
            }
        )
        return 0

    client = TapdClient(args.base_url)
    results: List[Dict[str, Any]] = []
    if "story" in spec:
        try:
            story = client.create_story(workspace_id, spec["story"])
            results.append(_story_result("story", workspace_id, story))
        except TapdError as exc:
            _print_json(
                {
                    "status": "failed",
                    "mode": "create",
                    "workspace_id": workspace_id,
                    "results": results,
                    "failed": {"role": "story", "name": spec["story"]["name"], "error": str(exc)},
                    "not_attempted": [],
                }
            )
            return 2
    else:
        children = spec["children"]
        try:
            parent = client.create_story(workspace_id, spec["parent"])
            results.append(_story_result("parent", workspace_id, parent))
            parent_id = str(parent["id"])
        except TapdError as exc:
            _print_json(
                {
                    "status": "failed",
                    "mode": "create",
                    "workspace_id": workspace_id,
                    "results": results,
                    "failed": {"role": "parent", "name": spec["parent"]["name"], "error": str(exc)},
                    "not_attempted": [child["name"] for child in children],
                }
            )
            return 2

        for index, child in enumerate(children):
            role = f"child[{index + 1}]"
            try:
                created = client.create_story(workspace_id, child, parent_id)
                results.append(_story_result(role, workspace_id, created))
            except TapdError as exc:
                _print_json(
                    {
                        "status": "partial_failure",
                        "mode": "create",
                        "workspace_id": workspace_id,
                        "results": results,
                        "failed": {"role": role, "name": child["name"], "error": str(exc)},
                        "not_attempted": [item["name"] for item in children[index + 1 :]],
                    }
                )
                return 2

    _print_json(
        {
            "status": "success",
            "mode": "create",
            "workspace_id": workspace_id,
            "created_by": "current_authenticated_tapd_user",
            "results": results,
        }
    )
    return 0


def command_update(args: argparse.Namespace) -> int:
    spec = _load_spec(args.spec)
    operations = _validate_spec("update", spec)
    workspace_id = _resolve_workspace_id(args.workspace_id, spec)
    if args.dry_run:
        _print_json(
            {
                "status": "dry_run",
                "mode": "update",
                "workspace_id": workspace_id,
                "operations": operations,
                "writes_performed": False,
            }
        )
        return 0

    client = TapdClient(args.base_url)
    results: List[Dict[str, Any]] = []
    stories = spec["stories"]
    for index, item in enumerate(stories):
        role = f"stories[{index + 1}]"
        try:
            updated = client.update_story(workspace_id, item)
            results.append(_story_result(role, workspace_id, updated))
        except TapdError as exc:
            _print_json(
                {
                    "status": "partial_failure" if results else "failed",
                    "mode": "update",
                    "workspace_id": workspace_id,
                    "results": results,
                    "failed": {"role": role, "id": item["id"], "error": str(exc)},
                    "not_attempted": [entry["id"] for entry in stories[index + 1 :]],
                }
            )
            return 2

    _print_json(
        {
            "status": "success",
            "mode": "update",
            "workspace_id": workspace_id,
            "results": results,
        }
    )
    return 0


def main() -> int:
    args = parse_args()
    commands = {
        "detail": command_detail,
        "plan-context": command_plan_context,
        "validate-spec": command_validate_spec,
        "create": command_create,
        "update": command_update,
    }
    try:
        return commands[args.command](args)
    except (TapdError, RuntimeError) as exc:
        print(f"[!] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
