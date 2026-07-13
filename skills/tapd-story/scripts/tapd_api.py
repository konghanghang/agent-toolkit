#!/usr/bin/env python3
"""TAPD OpenAPI client shared by the tapd-story commands."""

from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "https://api.tapd.cn"
DEFAULT_FIELDS = (
    "id,name,description,status,status_label,priority,priority_label,"
    "owner,creator,developer,version,iteration_id,created,modified"
)


class TapdError(RuntimeError):
    """Raised when TAPD authentication, transport, or response validation fails."""


def _auth_header() -> str:
    token = os.getenv("TAPD_TOKEN", "").strip()
    if token:
        return f"Bearer {token}"

    api_user = os.getenv("TAPD_API_USER", "").strip()
    api_password = os.getenv("TAPD_API_PASSWORD", "").strip()
    if api_user and api_password:
        encoded = base64.b64encode(f"{api_user}:{api_password}".encode("utf-8")).decode("ascii")
        return f"Basic {encoded}"

    raise TapdError(
        "缺少 TAPD 认证信息，请设置 TAPD_TOKEN，"
        "或同时设置 TAPD_API_USER 和 TAPD_API_PASSWORD。"
    )


def _extract_story(data: Any) -> Optional[Dict[str, Any]]:
    if isinstance(data, list):
        for item in data:
            story = _extract_story(item)
            if story:
                return story
        return None

    if not isinstance(data, dict):
        return None

    for key in ("Story", "story"):
        value = data.get(key)
        if isinstance(value, dict):
            return value

    nested_data = data.get("data")
    if isinstance(nested_data, (dict, list)):
        nested = _extract_story(nested_data)
        if nested:
            return nested

    if "id" in data and ("name" in data or "description" in data):
        return data

    for value in data.values():
        if isinstance(value, (dict, list)):
            nested = _extract_story(value)
            if nested:
                return nested
    return None


def _detect_image_extension(data: bytes, filename: str) -> str:
    signatures = (
        (b"\x89PNG\r\n\x1a\n", ".png"),
        (b"\xff\xd8\xff", ".jpg"),
        (b"GIF87a", ".gif"),
        (b"GIF89a", ".gif"),
        (b"BM", ".bmp"),
    )
    for signature, extension in signatures:
        if data.startswith(signature):
            return extension

    suffix = Path(filename).suffix.lower()
    if suffix in {".png", ".gif", ".jpg", ".jpeg", ".bmp"}:
        return ".jpg" if suffix == ".jpeg" else suffix
    raise TapdError("下载内容不是支持的图片格式。")


def _safe_filename(value: str, fallback: str) -> str:
    name = Path(value or fallback).name
    name = re.sub(r"[^0-9A-Za-z._-]+", "-", name).strip(".-")
    return name or fallback


class TapdClient:
    def __init__(self, base_url: Optional[str] = None, timeout: int = 30) -> None:
        self.base_url = (base_url or os.getenv("TAPD_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        form: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        if params:
            url = f"{url}?{urlencode(params)}"

        data = None
        headers = {
            "Authorization": _auth_header(),
            "Accept": "application/json",
        }
        if form is not None:
            data = urlencode({key: str(value) for key, value in form.items()}).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"

        request = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise TapdError(f"TAPD HTTP {exc.code}: {body or exc.reason}") from exc
        except URLError as exc:
            raise TapdError(f"TAPD 请求失败: {exc.reason}") from exc

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise TapdError(f"TAPD 返回了非 JSON 内容: {raw[:200]}") from exc

        if not isinstance(payload, dict):
            raise TapdError("TAPD 返回结构不是对象。")
        if payload.get("status") != 1:
            info = payload.get("info") or payload.get("message") or "Unknown TAPD API error"
            raise TapdError(str(info))
        return payload

    def get_story(self, workspace_id: str, story_id: str, fields: str = DEFAULT_FIELDS) -> tuple[Dict[str, Any], Dict[str, Any]]:
        payload = self._request_json(
            "GET",
            "/stories",
            params={"workspace_id": workspace_id, "id": story_id, "fields": fields},
        )
        story = _extract_story(payload.get("data"))
        if not story:
            raise TapdError(f"工作区 {workspace_id} 中未找到需求 {story_id}。")
        return payload, story

    def create_story(
        self,
        workspace_id: str,
        item: Dict[str, Any],
        parent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        form: Dict[str, Any] = {
            "workspace_id": workspace_id,
            "name": item["name"],
            "description": item["description_html"],
        }
        if parent_id:
            form["parent_id"] = parent_id
        if item.get("owner"):
            form["owner"] = item["owner"]
        payload = self._request_json("POST", "/stories", form=form)
        story = _extract_story(payload.get("data"))
        if not story or not story.get("id"):
            raise TapdError(f"创建需求 {item['name']} 后未返回 Story。")
        return story

    def update_story(self, workspace_id: str, item: Dict[str, Any]) -> Dict[str, Any]:
        form: Dict[str, Any] = {"workspace_id": workspace_id, "id": item["id"]}
        mapping = {"name": "name", "description_html": "description", "owner": "owner"}
        for source, target in mapping.items():
            if source in item:
                form[target] = item[source]
        payload = self._request_json("POST", "/stories", form=form)
        story = _extract_story(payload.get("data"))
        if not story or not story.get("id"):
            raise TapdError(f"更新需求 {item['id']} 后未返回 Story。")
        return story

    def get_image_info(self, workspace_id: str, image_path: str) -> Dict[str, Any]:
        normalized_path = image_path.split("?", 1)[0]
        payload = self._request_json(
            "GET",
            "/files/get_image",
            params={"workspace_id": workspace_id, "image_path": normalized_path},
        )
        data = payload.get("data")
        attachment = data.get("Attachment") if isinstance(data, dict) else None
        if not isinstance(attachment, dict) or not attachment.get("download_url"):
            raise TapdError(f"图片 {image_path} 未返回临时下载地址。")
        return attachment

    def download_image(
        self,
        workspace_id: str,
        image_path: str,
        output_dir: Path,
        index: int,
    ) -> Dict[str, Any]:
        attachment = self.get_image_info(workspace_id, image_path)
        request = Request(str(attachment["download_url"]), headers={"Accept": "image/*"})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                data = response.read()
                content_type = response.headers.get("Content-Type", "")
        except HTTPError as exc:
            raise TapdError(f"图片下载 HTTP {exc.code}: {image_path}") from exc
        except URLError as exc:
            raise TapdError(f"图片下载失败 {image_path}: {exc.reason}") from exc

        filename = str(attachment.get("filename") or f"image-{index}")
        extension = _detect_image_extension(data, filename)
        safe_name = _safe_filename(filename, f"image-{index}{extension}")
        if Path(safe_name).suffix.lower() != extension:
            safe_name = f"{Path(safe_name).stem}{extension}"

        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{index:02d}-{safe_name}"
        output_path.write_bytes(data)
        return {
            "local_path": str(output_path),
            "filename": safe_name,
            "content_type": content_type,
            "size": len(data),
            "source_path": image_path,
        }


def story_link(workspace_id: str, story_id: str) -> str:
    return f"https://www.tapd.cn/{workspace_id}/prong/stories/view/{story_id}"
