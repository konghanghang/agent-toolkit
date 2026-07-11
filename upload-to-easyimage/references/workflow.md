# EasyImage 上传工作流

## 配置

首次安装、配置文件位置、最小 JSON 示例和字段含义统一见 [picgo-config.md](picgo-config.md)。本工作流假定 `picBed.web-uploader` 已配置完成；异常时先运行 `--check-config`，不要直接输出配置中的 Header 或 Body 值。

## 上传结果

每个输入文件返回：

- `source`：原始本地文件
- `upload_file`：实际上传文件；SVG 转换时为生成的 PNG
- `converted`：是否执行了转换
- `local_format`、`local_width`、`local_height`：上传前识别结果
- `easyimage_url`：正式访问地址
- `thumb`：EasyImage 缩略图地址
- `src_name`：服务端返回的文件名
- `verification`：远程状态、响应头、真实格式、尺寸和字节数

## 验证原则

不要只信任响应头。某些服务会返回类似：

```text
image/webp;text/html; charset=utf-8
```

脚本会下载响应体，通过文件魔数识别 PNG、JPEG、GIF、WebP、BMP、TIFF、ICO、AVIF 或 SVG。若下载结果是 HTML 或未知内容，验证失败。

服务端可能把上传的 PNG 自动转成 WebP；只要远程文件是有效图片且满足调用方要求，这不等同于上传失败。

## SVG 转换

默认上传原始 SVG。只有显式传入 `--convert-svg` 时才调用 `rsvg-convert` 生成 PNG。

- `--width` 与 `--height` 必须同时提供，或同时省略。
- 同时省略时按 SVG 自身尺寸导出。
- 转换文件默认写入 `$TMP_DIR/easyimage-upload/`；未设置 `TMP_DIR` 时使用系统临时目录。
- `--keep-existing` 可以复用同名转换文件。

## 常见错误

- `PicGo config not found`：用 `--config` 指定配置，或创建 `~/.picgo/config.json`。
- `Missing picBed.web-uploader`：检查 PicGo 当前上传器配置。
- `Invalid customHeader/customBody JSON`：字段必须是对象或能解析为对象的 JSON 字符串。
- `Required tool not found: curl`：安装 curl。
- `Required tool not found: rsvg-convert`：仅 SVG 转换需要；macOS 可通过 `brew install librsvg` 安装。
- `Upload response is not valid JSON`：检查上传地址、认证字段和服务端错误页。
- `Remote response is not a recognized image`：正式 URL 返回了 HTML、鉴权错误或不支持的文件格式。
