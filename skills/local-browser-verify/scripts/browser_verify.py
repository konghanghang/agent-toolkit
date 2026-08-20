#!/usr/bin/env python3
"""Prepare and finish a browser-verification run without driving a browser."""

from __future__ import annotations

import argparse
import fnmatch
import html
import ipaddress
import json
import os
import re
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BASELINE_VERSION = 1
MAX_RESPONSE_BYTES = 128 * 1024
ARTIFACT_DIRECTORY_NAMES = {
    ".playwright-mcp",
    "playwright-report",
    "test-results",
}
SKIPPED_DIRECTORY_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".next",
    ".nuxt",
    ".venv",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "target",
    "venv",
}
ROOT_ARTIFACT_PATTERNS = (
    "*.har",
    "page-*.png",
    "screenshot*.png",
    "trace*.zip",
)
TITLE_RE = re.compile(r"<title(?:\s[^>]*)?>(.*?)</title>", re.IGNORECASE | re.DOTALL)


class GuardedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject redirects that leave the allowed target scope."""

    def __init__(self, allow_remote: bool) -> None:
        super().__init__()
        self.allow_remote = allow_remote

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        validate_url(newurl, allow_remote=self.allow_remote)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def is_local_host(hostname: str) -> bool:
    normalized = hostname.rstrip(".").lower()
    if normalized == "localhost" or normalized.endswith(".localhost"):
        return True
    if normalized == "0.0.0.0":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def validate_url(url: str, *, allow_remote: bool) -> urllib.parse.SplitResult:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"Only http/https URLs are supported: {url}")
    if not parsed.hostname:
        raise ValueError(f"URL is missing a hostname: {url}")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Credentials must not be embedded in a verification URL")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError(f"Invalid URL port: {url}") from exc
    if not allow_remote and not is_local_host(parsed.hostname):
        raise ValueError(
            f"Remote target is not allowed by default: {parsed.hostname}. "
            "Use --allow-remote only when the user explicitly selected it."
        )
    return parsed


def redact_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    redacted_query = urllib.parse.urlencode(
        [(key, "REDACTED") for key, _ in urllib.parse.parse_qsl(
            parsed.query, keep_blank_values=True
        )]
    )
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, redacted_query, "")
    )


def extract_title(body: bytes, content_type: str) -> str | None:
    if "html" not in content_type.lower():
        return None
    charset_match = re.search(r"charset=([^;\s]+)", content_type, re.IGNORECASE)
    charset = charset_match.group(1).strip('"\'') if charset_match else "utf-8"
    try:
        text = body.decode(charset, errors="replace")
    except LookupError:
        text = body.decode("utf-8", errors="replace")
    match = TITLE_RE.search(text)
    if not match:
        return None
    return " ".join(html.unescape(match.group(1)).split())[:200] or None


def probe_url(
    url: str,
    *,
    allow_remote: bool = False,
    insecure: bool = False,
    timeout: float = 5.0,
) -> dict[str, Any]:
    validate_url(url, allow_remote=allow_remote)
    handlers: list[Any] = [GuardedRedirectHandler(allow_remote)]
    if insecure:
        handlers.append(urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))
    opener = urllib.request.build_opener(*handlers)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "local-browser-verify/1"},
        method="GET",
    )

    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read(MAX_RESPONSE_BYTES)
            status = response.status
            final_url = response.geturl()
            validate_url(final_url, allow_remote=allow_remote)
            content_type = response.headers.get("Content-Type", "")
            return {
                "requested_url": redact_url(url),
                "final_url": redact_url(final_url),
                "status": status,
                "ok": 200 <= status < 400,
                "content_type": content_type,
                "title": extract_title(body, content_type),
                "error": None,
            }
    except urllib.error.HTTPError as exc:
        body = exc.read(MAX_RESPONSE_BYTES)
        content_type = exc.headers.get("Content-Type", "") if exc.headers else ""
        return {
            "requested_url": redact_url(url),
            "final_url": redact_url(exc.geturl()),
            "status": exc.code,
            "ok": False,
            "content_type": content_type,
            "title": extract_title(body, content_type),
            "error": f"HTTP {exc.code}",
        }
    except (OSError, urllib.error.URLError, ValueError) as exc:
        return {
            "requested_url": redact_url(url),
            "final_url": None,
            "status": None,
            "ok": False,
            "content_type": None,
            "title": None,
            "error": str(exc),
        }


def resolve_repository(path: str | Path) -> Path:
    candidate = Path(path).expanduser().resolve()
    if not candidate.is_dir():
        raise ValueError(f"Repository directory does not exist: {candidate}")
    try:
        result = subprocess.run(
            ["git", "-C", str(candidate), "rev-parse", "--show-toplevel"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return candidate
    if result.returncode == 0 and result.stdout.strip():
        return Path(result.stdout.strip()).resolve()
    return candidate


def git_status(repository: Path) -> list[str]:
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=all",
            ],
            check=False,
            capture_output=True,
        )
    except OSError:
        return []
    if result.returncode != 0:
        return []
    decoded = result.stdout.decode("utf-8", errors="surrogateescape")
    return sorted(item for item in decoded.split("\0") if item)


def artifact_fingerprint(path: Path) -> dict[str, Any]:
    stat = path.lstat()
    if path.is_symlink():
        kind = "symlink"
    elif path.is_dir():
        kind = "directory"
    else:
        kind = "file"
    return {
        "kind": kind,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def is_root_artifact(relative_path: Path) -> bool:
    return len(relative_path.parts) == 1 and any(
        fnmatch.fnmatch(relative_path.name.lower(), pattern)
        for pattern in ROOT_ARTIFACT_PATTERNS
    )


def collect_artifacts(repository: Path) -> dict[str, dict[str, Any]]:
    artifacts: dict[str, dict[str, Any]] = {}
    for current_root, directory_names, file_names in os.walk(
        repository, followlinks=False
    ):
        current_path = Path(current_root)
        relative_root = current_path.relative_to(repository)
        inside_artifact_directory = any(
            part in ARTIFACT_DIRECTORY_NAMES for part in relative_root.parts
        )

        kept_directories: list[str] = []
        for directory_name in directory_names:
            child = current_path / directory_name
            relative_child = child.relative_to(repository)
            if directory_name in ARTIFACT_DIRECTORY_NAMES or inside_artifact_directory:
                artifacts[relative_child.as_posix()] = artifact_fingerprint(child)
                kept_directories.append(directory_name)
            elif directory_name not in SKIPPED_DIRECTORY_NAMES:
                kept_directories.append(directory_name)
        directory_names[:] = kept_directories

        for file_name in file_names:
            child = current_path / file_name
            relative_child = child.relative_to(repository)
            if inside_artifact_directory or is_root_artifact(relative_child):
                artifacts[relative_child.as_posix()] = artifact_fingerprint(child)
    return dict(sorted(artifacts.items()))


def default_baseline_path() -> Path:
    configured_tmp = os.environ.get("TMP_DIR")
    directory: str | None = None
    if configured_tmp:
        candidate = Path(configured_tmp).expanduser()
        if candidate.is_dir():
            directory = str(candidate)
    descriptor, raw_path = tempfile.mkstemp(
        prefix="local-browser-verify-",
        suffix=".json",
        dir=directory,
    )
    os.close(descriptor)
    return Path(raw_path)


def write_baseline(payload: dict[str, Any], destination: Path) -> None:
    if not destination.parent.is_dir():
        raise ValueError(f"Baseline parent directory does not exist: {destination.parent}")
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def prepare(
    repository: str | Path,
    urls: list[str],
    *,
    baseline_path: str | Path | None = None,
    allow_remote: bool = False,
    insecure: bool = False,
    timeout: float = 5.0,
) -> tuple[dict[str, Any], Path]:
    resolved_repository = resolve_repository(repository)
    probes = [
        probe_url(
            url,
            allow_remote=allow_remote,
            insecure=insecure,
            timeout=timeout,
        )
        for url in urls
    ]
    payload = {
        "version": BASELINE_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "repository": str(resolved_repository),
        "git_status": git_status(resolved_repository),
        "artifacts": collect_artifacts(resolved_repository),
        "probes": probes,
    }
    destination = (
        Path(baseline_path).expanduser().resolve()
        if baseline_path is not None
        else default_baseline_path()
    )
    write_baseline(payload, destination)
    return payload, destination


def read_baseline(path: str | Path) -> tuple[dict[str, Any], Path]:
    baseline_path = Path(path).expanduser().resolve()
    try:
        payload = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to read baseline {baseline_path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("version") != BASELINE_VERSION:
        raise ValueError(f"Unsupported browser verification baseline: {baseline_path}")
    if not isinstance(payload.get("repository"), str):
        raise ValueError(f"Baseline repository is invalid: {baseline_path}")
    if not isinstance(payload.get("artifacts"), dict):
        raise ValueError(f"Baseline artifacts are invalid: {baseline_path}")
    if not isinstance(payload.get("git_status"), list):
        raise ValueError(f"Baseline Git status is invalid: {baseline_path}")
    return payload, baseline_path


def finish(
    baseline: str | Path,
    *,
    repository: str | Path | None = None,
    remove_baseline: bool = False,
) -> dict[str, Any]:
    previous, baseline_path = read_baseline(baseline)
    previous_repository = Path(previous["repository"]).resolve()
    resolved_repository = (
        resolve_repository(repository) if repository is not None else previous_repository
    )
    if resolved_repository != previous_repository:
        raise ValueError(
            "Baseline repository mismatch: "
            f"expected {previous_repository}, got {resolved_repository}"
        )

    current_artifacts = collect_artifacts(resolved_repository)
    previous_artifacts = previous["artifacts"]
    previous_paths = set(previous_artifacts)
    current_paths = set(current_artifacts)
    current_status = git_status(resolved_repository)
    previous_status = set(previous["git_status"])

    result = {
        "repository": str(resolved_repository),
        "baseline": str(baseline_path),
        "created_artifacts": sorted(current_paths - previous_paths),
        "modified_artifacts": sorted(
            path
            for path in current_paths & previous_paths
            if current_artifacts[path] != previous_artifacts[path]
        ),
        "removed_artifacts": sorted(previous_paths - current_paths),
        "git_status_added": sorted(set(current_status) - previous_status),
        "git_status_resolved": sorted(previous_status - set(current_status)),
        "baseline_removed": False,
    }
    if remove_baseline:
        baseline_path.unlink()
        result["baseline_removed"] = True
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Probe a browser target and isolate verification artifacts."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser(
        "prepare", help="Probe URLs and write a pre-verification baseline"
    )
    prepare_parser.add_argument("--repo", default=".", help="Project directory")
    prepare_parser.add_argument(
        "--url", action="append", default=[], help="Target URL; repeat as needed"
    )
    prepare_parser.add_argument("--baseline", help="Explicit baseline output path")
    prepare_parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="Allow non-loopback targets selected by the user",
    )
    prepare_parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS certificate verification for an explicitly trusted target",
    )
    prepare_parser.add_argument(
        "--timeout", type=float, default=5.0, help="HTTP timeout in seconds"
    )
    prepare_parser.add_argument("--json", action="store_true", help="Print JSON")

    finish_parser = subparsers.add_parser(
        "finish", help="Compare the workspace with a pre-verification baseline"
    )
    finish_parser.add_argument("--baseline", required=True, help="Baseline path")
    finish_parser.add_argument("--repo", help="Project directory for mismatch checks")
    finish_parser.add_argument(
        "--remove-baseline",
        action="store_true",
        help="Remove the baseline after a successful comparison",
    )
    finish_parser.add_argument("--json", action="store_true", help="Print JSON")
    return parser


def print_prepare(payload: dict[str, Any], baseline: Path) -> None:
    print(f"baseline: {baseline}")
    print(f"repository: {payload['repository']}")
    print(f"git_entries_before: {len(payload['git_status'])}")
    print(f"artifacts_before: {len(payload['artifacts'])}")
    if not payload["probes"]:
        print("probe: skipped (no --url supplied)")
    for probe in payload["probes"]:
        status = probe["status"] if probe["status"] is not None else "ERROR"
        detail = probe["title"] or probe["error"] or probe["final_url"] or ""
        print(f"probe: {status} {probe['requested_url']} {detail}".rstrip())


def print_finish(result: dict[str, Any]) -> None:
    print(f"repository: {result['repository']}")
    for key in (
        "created_artifacts",
        "modified_artifacts",
        "removed_artifacts",
        "git_status_added",
        "git_status_resolved",
    ):
        values = result[key]
        print(f"{key}: {len(values)}")
        for value in values:
            print(f"  - {value}")
    print(f"baseline_removed: {str(result['baseline_removed']).lower()}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            payload, baseline = prepare(
                args.repo,
                args.url,
                baseline_path=args.baseline,
                allow_remote=args.allow_remote,
                insecure=args.insecure,
                timeout=args.timeout,
            )
            if args.json:
                output = dict(payload)
                output["baseline"] = str(baseline)
                print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                print_prepare(payload, baseline)
            return 0 if all(probe["ok"] for probe in payload["probes"]) else 1

        result = finish(
            args.baseline,
            repository=args.repo,
            remove_baseline=args.remove_baseline,
        )
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print_finish(result)
        return 0
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
