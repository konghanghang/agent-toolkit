#!/usr/bin/env python3
"""TAPD story content normalization, extraction, and validation helpers."""

from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser
from typing import Any, Dict, Iterable, List


EMPTY_DETAIL_TEXT = "TAPD 未返回需求详细内容。"
IMAGE_CONTEXT_WINDOW = 120
FIELD_CODE_IGNORE = {
    "string",
    "integer",
    "number",
    "boolean",
    "json",
    "http",
    "https",
    "tapd",
    "api",
    "url",
}
ALLOWED_HTML_TAGS = {
    "h3",
    "p",
    "ul",
    "ol",
    "li",
    "strong",
    "code",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
    "br",
    "a",
}
ALLOWED_HTML_ATTRIBUTES = {
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
    "a": {"href"},
}
IGNORED_URLS = {
    "http://www.w3.org/2000/svg",
    "http://www.w3.org/1999/xlink",
}
SENSITIVE_VALUE_PATTERN = re.compile(
    r"(?i)((?:密码|口令|password|passwd|token|secret|cookie|session)\s*[:：]\s*)([^\s,，;；<]+)"
)


def html_to_text(value: Any) -> str:
    if not value:
        return ""

    text = str(value)
    replacements = [
        (r"<\s*br\s*/?\s*>", "\n"),
        (r"<\s*/\s*p\s*>", "\n"),
        (r"<\s*/\s*div\s*>", "\n"),
        (r"<\s*/\s*li\s*>", "\n"),
        (r"<\s*li\b[^>]*>", "- "),
        (r"<\s*/\s*tr\s*>", "\n"),
        (r"<\s*/\s*h[1-6]\s*>", "\n"),
        (r"<\s*(p|div|tr|h[1-6])\b[^>]*>", ""),
        (r"<\s*(td|th)\b[^>]*>", " "),
        (r"<\s*/\s*(td|th)\s*>", " "),
    ]
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.I)

    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def normalize_text(value: Any, default: str = "") -> str:
    text = html_to_text(value)
    return text if text else default


def redact_sensitive_text(value: str) -> str:
    return SENSITIVE_VALUE_PATTERN.sub(lambda match: f"{match.group(1)}***", value)


def normalize_status(value: Any) -> str:
    text = normalize_text(value)
    return "" if re.fullmatch(r"status_\d+", text) else text


def pick_first(story: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = story.get(key)
        if value not in (None, ""):
            return value
    return ""


def to_list_text(value: Any) -> str:
    text = normalize_text(value)
    if not text:
        return ""
    parts = re.split(r"[;,，]", text)
    return ", ".join(part.strip() for part in parts if part.strip())


def extract_urls(raw_html: Any) -> List[str]:
    seen = set()
    urls = []
    for match in re.findall(r"https?://[^\s<>'\")）]+", str(raw_html or ""), flags=re.I):
        value = unescape(match).rstrip(".,;，。；")
        if value not in IGNORED_URLS and value not in seen:
            seen.add(value)
            urls.append(value)
    return urls


def extract_field_codes(detail: str) -> List[str]:
    codes = []
    seen = set()
    for raw_line in detail.splitlines():
        line = raw_line.strip().strip("`|:")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{1,63}", line):
            continue
        if line.lower() in FIELD_CODE_IGNORE or line in seen:
            continue
        seen.add(line)
        codes.append(line)
        if len(codes) >= 20:
            break
    return codes


def extract_images(raw_html: Any) -> List[Dict[str, Any]]:
    html = str(raw_html or "")
    images = []
    seen = set()
    pattern = re.compile(r"<img[^>]+(?:src|data-src)=[\"']([^\"']+)[\"'][^>]*>", re.I)
    for match in pattern.finditer(html):
        src = unescape(match.group(1).strip())
        if not src or src in seen:
            continue
        seen.add(src)
        before = normalize_text(html[: match.start()])[-IMAGE_CONTEXT_WINDOW:]
        after = normalize_text(html[match.end() :])[:IMAGE_CONTEXT_WINDOW]
        context = redact_sensitive_text("\n".join(part for part in (before, after) if part).strip())
        images.append(
            {
                "index": len(images) + 1,
                "src": src,
                "context": context,
            }
        )
    return images


def _clean_title_for_search(title: str) -> str:
    title = re.sub(r"【[^】]+】", " ", title)
    title = re.sub(r"[（）()\-—_/]+", " ", title)
    return re.sub(r"\s+", " ", title).strip()


def build_search_terms(title: str, field_codes: Iterable[str]) -> List[str]:
    terms = []
    seen = set()

    def add(value: str) -> None:
        value = value.strip()
        if len(value) < 2 or value in seen:
            return
        seen.add(value)
        terms.append(value)

    add(title)
    clean_title = _clean_title_for_search(title)
    add(clean_title)
    for chunk in re.split(r"\s+", clean_title):
        add(chunk)
    for code in field_codes:
        add(code)
    return terms[:15]


def build_story_view(story_id: str, workspace_id: str, story: Dict[str, Any]) -> Dict[str, Any]:
    title = normalize_text(pick_first(story, "name"), default=f"未命名需求 {story_id}")
    raw_description = pick_first(story, "description")
    detail = redact_sensitive_text(normalize_text(raw_description, default=EMPTY_DETAIL_TEXT))
    field_codes = extract_field_codes(detail)
    return {
        "story_id": str(story_id),
        "workspace_id": str(workspace_id),
        "title": title,
        "detail": detail,
        "status": normalize_status(pick_first(story, "status_label", "status")),
        "priority": normalize_text(pick_first(story, "priority_label", "priority")),
        "owner": to_list_text(pick_first(story, "owner")),
        "creator": normalize_text(pick_first(story, "creator")),
        "developer": to_list_text(pick_first(story, "developer")),
        "version": normalize_text(pick_first(story, "version")),
        "iteration_id": normalize_text(pick_first(story, "iteration_id")),
        "created": normalize_text(pick_first(story, "created")),
        "modified": normalize_text(pick_first(story, "modified")),
        "urls": extract_urls(raw_description),
        "field_codes": field_codes,
        "search_terms": build_search_terms(title, field_codes),
        "images": extract_images(raw_description),
    }


class _HtmlValidator(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.errors: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[tuple[str, str | None]]) -> None:
        normalized = tag.lower()
        if normalized not in ALLOWED_HTML_TAGS:
            self.errors.append(f"不允许的 HTML 标签: {tag}")
            return
        allowed_attributes = ALLOWED_HTML_ATTRIBUTES.get(normalized, set())
        for name, value in attrs:
            if name.lower() not in allowed_attributes:
                self.errors.append(f"标签 {tag} 不允许属性 {name}")
            if normalized == "a" and name.lower() == "href":
                href = str(value or "").strip().lower()
                if not href.startswith(("http://", "https://")):
                    self.errors.append("标签 a 的 href 只允许 HTTP/HTTPS 地址")

    def handle_startendtag(self, tag: str, attrs: List[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)


def validate_description_html(value: Any) -> List[str]:
    html = str(value or "").strip()
    if not html:
        return ["description_html 不能为空"]
    validator = _HtmlValidator()
    try:
        validator.feed(html)
        validator.close()
    except Exception as exc:
        validator.errors.append(f"HTML 解析失败: {exc}")
    if not html_to_text(html):
        validator.errors.append("description_html 不包含可读文本")
    return list(dict.fromkeys(validator.errors))
