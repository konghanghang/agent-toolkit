#!/usr/bin/env python3
import argparse
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from shutil import which
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse


SUPPORTED_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
    ".ico",
    ".avif",
    ".svg",
}


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def ensure_tool(name: str) -> None:
    if not which(name):
        fail(f"Required tool not found: {name}")


def parse_json_object(raw: Any, label: str) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if raw in (None, ""):
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        fail(f"Invalid {label} JSON in PicGo config: {exc}")
    if not isinstance(parsed, dict):
        fail(f"{label} must parse to an object")
    return parsed


def load_picgo_config(config_path: Path) -> Dict[str, Any]:
    if not config_path.exists():
        fail(f"PicGo config not found: {config_path}")

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"PicGo config is not valid JSON: {exc}")

    uploader = data.get("picBed", {}).get("web-uploader")
    if not isinstance(uploader, dict):
        fail(f"Missing picBed.web-uploader in {config_path}")
    if not uploader.get("url"):
        fail("Missing picBed.web-uploader.url in PicGo config")

    return {
        "config_path": str(config_path),
        "url": uploader["url"],
        "param_name": uploader.get("paramName", "image"),
        "json_path": uploader.get("jsonPath", "url"),
        "custom_body": parse_json_object(uploader.get("customBody"), "customBody"),
        "custom_header": parse_json_object(uploader.get("customHeader"), "customHeader"),
    }


def sanitized_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "config_path": cfg["config_path"],
        "url": cfg["url"],
        "param_name": cfg["param_name"],
        "json_path": cfg["json_path"],
        "custom_header_keys": sorted(cfg["custom_header"].keys()),
        "custom_body_keys": sorted(cfg["custom_body"].keys()),
    }


def extract_json_path(data: Any, json_path: str) -> Any:
    current = data
    for part in json_path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            fail(
                f"Could not resolve jsonPath '{json_path}' in response: "
                f"{json.dumps(data, ensure_ascii=False)}"
            )
    return current


def parse_jpeg_size(data: bytes) -> Tuple[Optional[int], Optional[int]]:
    index = 2
    sof_markers = {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
    while index + 4 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        while index < len(data) and data[index] == 0xFF:
            index += 1
        if index >= len(data):
            break
        marker = data[index]
        index += 1
        if marker in {0x01, 0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            continue
        if index + 2 > len(data):
            break
        segment_length = int.from_bytes(data[index : index + 2], "big")
        if segment_length < 2 or index + segment_length > len(data):
            break
        if marker in sof_markers and segment_length >= 7:
            height = int.from_bytes(data[index + 3 : index + 5], "big")
            width = int.from_bytes(data[index + 5 : index + 7], "big")
            return width, height
        index += segment_length
    return None, None


def parse_svg_size(data: bytes) -> Tuple[Optional[int], Optional[int]]:
    text = data[:65536].decode("utf-8", errors="ignore")
    svg_match = re.search(r"<svg\b([^>]*)>", text, re.IGNORECASE | re.DOTALL)
    if not svg_match:
        return None, None
    attrs = svg_match.group(1)

    def number(name: str) -> Optional[int]:
        match = re.search(rf"\b{name}\s*=\s*['\"]\s*([0-9]+(?:\.[0-9]+)?)", attrs, re.IGNORECASE)
        return round(float(match.group(1))) if match else None

    width = number("width")
    height = number("height")
    if width and height:
        return width, height

    view_box = re.search(
        r"\bviewBox\s*=\s*['\"]\s*[-+0-9.eE]+[ ,]+[-+0-9.eE]+[ ,]+([-+0-9.eE]+)[ ,]+([-+0-9.eE]+)",
        attrs,
        re.IGNORECASE,
    )
    if view_box:
        return round(float(view_box.group(1))), round(float(view_box.group(2)))
    return width, height


def detect_image(data: bytes) -> Dict[str, Optional[Any]]:
    fmt: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None

    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        fmt = "png"
        width, height = struct.unpack(">II", data[16:24])
    elif data.startswith(b"\xff\xd8"):
        fmt = "jpeg"
        width, height = parse_jpeg_size(data)
    elif data[:6] in {b"GIF87a", b"GIF89a"} and len(data) >= 10:
        fmt = "gif"
        width, height = struct.unpack("<HH", data[6:10])
    elif data.startswith(b"RIFF") and data[8:12] == b"WEBP" and len(data) >= 30:
        fmt = "webp"
        chunk = data[12:16]
        if chunk == b"VP8X":
            width = 1 + int.from_bytes(data[24:27], "little")
            height = 1 + int.from_bytes(data[27:30], "little")
        elif chunk == b"VP8 " and data[23:26] == b"\x9d\x01\x2a":
            width = int.from_bytes(data[26:28], "little") & 0x3FFF
            height = int.from_bytes(data[28:30], "little") & 0x3FFF
        elif chunk == b"VP8L" and data[20] == 0x2F:
            bits = int.from_bytes(data[21:25], "little")
            width = 1 + (bits & 0x3FFF)
            height = 1 + ((bits >> 14) & 0x3FFF)
    elif data.startswith(b"BM") and len(data) >= 26:
        fmt = "bmp"
        width = int.from_bytes(data[18:22], "little", signed=True)
        height = abs(int.from_bytes(data[22:26], "little", signed=True))
    elif data.startswith((b"II*\x00", b"MM\x00*")):
        fmt = "tiff"
    elif data.startswith(b"\x00\x00\x01\x00") and len(data) >= 8:
        fmt = "ico"
        width = data[6] or 256
        height = data[7] or 256
    elif len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] in {b"avif", b"avis"}:
        fmt = "avif"
    else:
        stripped = data.lstrip(b"\xef\xbb\xbf \t\r\n")
        if stripped.startswith(b"<svg") or (stripped.startswith(b"<?xml") and b"<svg" in stripped[:65536]):
            fmt = "svg"
            width, height = parse_svg_size(data)

    return {"format": fmt, "width": width, "height": height}


def inspect_local_image(path: Path, max_bytes: int) -> Dict[str, Optional[Any]]:
    with path.open("rb") as handle:
        data = handle.read(max_bytes)
    detected = detect_image(data)
    if not detected["format"]:
        fail(f"Input is not a recognized image: {path}")
    return detected


def convert_svg_to_png(
    source: Path,
    destination: Path,
    width: Optional[int],
    height: Optional[int],
) -> None:
    ensure_tool("rsvg-convert")
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = ["rsvg-convert"]
    if width is not None and height is not None:
        command.extend(["-w", str(width), "-h", str(height)])
    command.extend([str(source), "-o", str(destination)])
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        fail(f"SVG conversion failed for {source}: {result.stderr.strip()}")


def upload_file(path: Path, cfg: Dict[str, Any]) -> Dict[str, Any]:
    ensure_tool("curl")
    command: List[str] = ["curl", "-sS", "-X", "POST"]
    for key, value in cfg["custom_header"].items():
        command.extend(["-H", f"{key}: {value}"])
    command.extend(["-F", f"{cfg['param_name']}=@{path}"])
    for key, value in cfg["custom_body"].items():
        command.extend(["-F", f"{key}={value}"])
    command.append(cfg["url"])

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        fail(f"Upload request failed for {path}: {result.stderr.strip()}")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        fail(f"Upload response is not valid JSON: {exc}\n{result.stdout}")
    if "result" in data and data.get("result") != "success":
        fail(f"Upload failed: {result.stdout}")

    final_url = extract_json_path(data, cfg["json_path"])
    if not isinstance(final_url, str) or urlparse(final_url).scheme not in {"http", "https"}:
        fail(f"Resolved jsonPath '{cfg['json_path']}' is not an HTTP URL")
    data["_resolved_url"] = final_url
    return data


def verify_remote_image(url: str, timeout: int, max_bytes: int) -> Dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "upload-to-easyimage/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(max_bytes + 1)
            status = getattr(response, "status", None)
            content_type = response.headers.get("Content-Type")
            final_url = response.geturl()
    except (urllib.error.URLError, TimeoutError) as exc:
        fail(f"Remote image verification failed for {url}: {exc}")

    if len(body) > max_bytes:
        fail(f"Remote image exceeds verification limit of {max_bytes} bytes: {url}")
    detected = detect_image(body)
    if not detected["format"]:
        preview = body[:120].decode("utf-8", errors="replace")
        fail(f"Remote response is not a recognized image: {url}\nPreview: {preview!r}")

    return {
        "http_status": status,
        "final_url": final_url,
        "content_type": content_type,
        "detected_format": detected["format"],
        "width": detected["width"],
        "height": detected["height"],
        "bytes": len(body),
    }


def main() -> None:
    default_config = Path.home() / ".picgo" / "config.json"
    temp_root = Path(os.environ.get("TMP_DIR", tempfile.gettempdir())) / "easyimage-upload"

    parser = argparse.ArgumentParser(
        description="Upload local images to EasyImage through PicGo web-uploader config and verify the returned URLs."
    )
    parser.add_argument("inputs", nargs="*", help="Local image files to upload")
    parser.add_argument("--config", default=str(default_config), help="PicGo config path")
    parser.add_argument("--check-config", action="store_true", help="Validate and print sanitized uploader config")
    parser.add_argument("--convert-svg", action="store_true", help="Convert SVG inputs to PNG before upload")
    parser.add_argument("--width", type=int, help="PNG width for SVG conversion; requires --height")
    parser.add_argument("--height", type=int, help="PNG height for SVG conversion; requires --width")
    parser.add_argument("--output-dir", default=str(temp_root), help="Directory for converted PNG files")
    parser.add_argument("--keep-existing", action="store_true", help="Reuse an existing converted PNG")
    parser.add_argument("--no-verify", action="store_true", help="Skip downloading and validating returned URLs")
    parser.add_argument("--timeout", type=int, default=30, help="Remote verification timeout in seconds")
    parser.add_argument(
        "--max-download-bytes",
        type=int,
        default=50 * 1024 * 1024,
        help="Maximum bytes downloaded while verifying each remote image",
    )
    args = parser.parse_args()

    if (args.width is None) != (args.height is None):
        fail("--width and --height must be provided together")
    if (args.width is not None or args.height is not None) and not args.convert_svg:
        fail("--width and --height require --convert-svg")

    config_path = Path(args.config).expanduser().resolve()
    cfg = load_picgo_config(config_path)
    if args.check_config:
        print(json.dumps(sanitized_config(cfg), ensure_ascii=False, indent=2))
        if not args.inputs:
            return
    if not args.inputs:
        fail("Provide at least one image, or use --check-config")

    output_dir = Path(args.output_dir).expanduser().resolve()
    results = []

    for raw_input in args.inputs:
        source = Path(raw_input).expanduser().resolve()
        if not source.is_file():
            fail(f"Input file not found: {source}")
        if source.suffix.lower() not in SUPPORTED_SUFFIXES:
            fail(f"Unsupported image extension: {source.suffix or '<none>'}")

        upload_path = source
        converted = False
        if source.suffix.lower() == ".svg" and args.convert_svg:
            upload_path = output_dir / f"{source.stem}.png"
            if not (args.keep_existing and upload_path.exists()):
                convert_svg_to_png(source, upload_path, args.width, args.height)
            converted = True

        local = inspect_local_image(upload_path, args.max_download_bytes)
        response = upload_file(upload_path, cfg)
        final_url = response["_resolved_url"]
        verification = None
        if not args.no_verify:
            verification = verify_remote_image(final_url, args.timeout, args.max_download_bytes)

        results.append(
            {
                "source": str(source),
                "upload_file": str(upload_path),
                "converted": converted,
                "local_format": local["format"],
                "local_width": local["width"],
                "local_height": local["height"],
                "easyimage_url": final_url,
                "thumb": response.get("thumb"),
                "src_name": response.get("srcName"),
                "verification": verification,
            }
        )

    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
