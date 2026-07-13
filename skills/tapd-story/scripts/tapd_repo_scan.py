#!/usr/bin/env python3
"""Repository search helpers for TAPD implementation context."""

from __future__ import annotations

import subprocess
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


MAX_HITS_PER_TERM = 20
MAX_DISPLAY_HITS = 30
MAX_CANDIDATE_FILES = 15
MAX_PROJECTS = 10
GENERIC_REPO_TERMS = {
    "http",
    "https",
    "get",
    "post",
    "put",
    "delete",
    "patch",
    "string",
    "json",
    "platform",
    "data",
}


def classify_path(path: str) -> str:
    normalized = path.replace("\\", "/").lower()
    suffix = Path(normalized).suffix
    if (
        normalized.startswith(("test/", "tests/"))
        or "/test/" in normalized
        or "/tests/" in normalized
        or suffix in {".spec", ".test"}
    ):
        return "test"
    if "/docs/" in normalized or "/doc/" in normalized or suffix in {".md", ".rst", ".adoc"}:
        return "doc"
    if suffix in {".yml", ".yaml", ".xml", ".properties", ".toml", ".ini", ".conf", ".json"}:
        return "config"
    if suffix in {
        ".java",
        ".kt",
        ".kts",
        ".groovy",
        ".py",
        ".go",
        ".rs",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".vue",
        ".php",
        ".rb",
        ".cs",
        ".cpp",
        ".c",
        ".h",
        ".sh",
    }:
        return "source"
    return "other"


def _run_rg(term: str, root: Path) -> List[Dict[str, str]]:
    try:
        result = subprocess.run(
            [
                "rg",
                "-n",
                "-F",
                "-m",
                str(MAX_HITS_PER_TERM),
                "--color",
                "never",
                term,
                str(root),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("tapd-story 的仓库检索需要安装 rg。") from exc

    if result.returncode not in (0, 1):
        raise RuntimeError(result.stderr.strip() or f"rg 检索失败: {term}")

    hits = []
    for line in result.stdout.splitlines():
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        path, lineno, snippet = parts
        hits.append(
            {
                "path": path,
                "line": lineno,
                "snippet": snippet.strip(),
                "term": term,
                "category": classify_path(path),
            }
        )
    return hits


def search_root(root: Path, terms: Iterable[str]) -> List[Dict[str, str]]:
    unique = OrderedDict()
    for term in terms:
        term = term.strip()
        if len(term) < 2:
            continue
        for hit in _run_rg(term, root):
            key = (hit["path"], hit["line"], hit["snippet"])
            unique.setdefault(key, hit)
    return list(unique.values())


def build_repo_terms(story_view: Dict[str, Any]) -> List[str]:
    terms = [str(story_view["story_id"])]
    seen = set(terms)
    for term in story_view.get("search_terms", []):
        term = str(term).strip()
        if len(term) < 3 or term.lower() in GENERIC_REPO_TERMS or term in seen:
            continue
        seen.add(term)
        terms.append(term)
    return terms[:10]


def rank_candidate_files(hits: Iterable[Dict[str, str]]) -> List[Dict[str, Any]]:
    ranked: OrderedDict[str, Dict[str, Any]] = OrderedDict()
    for hit in hits:
        path = hit["path"]
        item = ranked.setdefault(
            path,
            {
                "path": path,
                "category": hit["category"],
                "hit_count": 0,
                "terms": [],
            },
        )
        item["hit_count"] += 1
        if hit["term"] not in item["terms"]:
            item["terms"].append(hit["term"])

    category_weight = {"source": 4, "config": 3, "test": 2, "doc": 1, "other": 0}
    candidates = sorted(
        ranked.values(),
        key=lambda item: (
            -len(item["terms"]),
            -item["hit_count"],
            -category_weight.get(item["category"], 0),
            len(item["path"]),
            item["path"],
        ),
    )
    return candidates[:MAX_CANDIDATE_FILES]


def detect_project_root(path: str, workspace_root: Path) -> Path:
    current = Path(path).resolve().parent
    workspace = workspace_root.resolve()
    for parent in (current, *current.parents):
        if (parent / ".git").exists():
            return parent
        if parent == workspace:
            break
    return current


def summarize_projects(hits: Iterable[Dict[str, str]], workspace_root: Path) -> List[Dict[str, Any]]:
    projects: OrderedDict[str, Dict[str, Any]] = OrderedDict()
    for hit in hits:
        root = str(detect_project_root(hit["path"], workspace_root))
        item = projects.setdefault(
            root,
            {
                "project": root,
                "hit_count": 0,
                "categories": OrderedDict(),
                "terms": [],
            },
        )
        item["hit_count"] += 1
        category = hit["category"]
        item["categories"][category] = item["categories"].get(category, 0) + 1
        if hit["term"] not in item["terms"]:
            item["terms"].append(hit["term"])

    ranked = sorted(
        projects.values(),
        key=lambda item: (-len(item["terms"]), -item["hit_count"], item["project"]),
    )
    for item in ranked:
        item["categories"] = dict(item["categories"])
    return ranked[:MAX_PROJECTS]


def scan_context(
    story_view: Dict[str, Any],
    repo_root: Path,
    *,
    include_workspace: bool = False,
    workspace_root: Optional[Path] = None,
) -> Dict[str, Any]:
    terms = build_repo_terms(story_view)
    repo_hits = search_root(repo_root, terms)
    context: Dict[str, Any] = {
        "search_terms": terms,
        "repo_root": str(repo_root),
        "repo_hits": repo_hits[:MAX_DISPLAY_HITS],
        "repo_candidates": rank_candidate_files(repo_hits),
        "workspace_root": None,
        "workspace_hits": None,
        "workspace_projects": [],
    }

    if include_workspace:
        if workspace_root is None:
            raise RuntimeError("使用 --include-workspace 时必须提供 --workspace-root 或设置 WORK_ROOT。")
        workspace_hits = search_root(workspace_root, terms)
        context.update(
            {
                "workspace_root": str(workspace_root),
                "workspace_hits": workspace_hits[:MAX_DISPLAY_HITS],
                "workspace_projects": summarize_projects(workspace_hits, workspace_root),
            }
        )
    return context
