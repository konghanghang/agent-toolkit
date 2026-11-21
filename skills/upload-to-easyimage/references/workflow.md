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
- `verification`：远程状态、响应头、真实格式、尺寸、字节数和 `validation`；`decoded` 表示栅格图片已通过结构检查并解码所有帧，`svg_xml` 表示 SVG 的 XML 结构有效。显式跳过验证时为 `null`，验证失败时包含 `error`。

批量处理保持输入顺序，遇到首个错误停止并返回非零退出码。即使退出失败，只要已取得上传 URL，标准输出仍会返回 JSON 数组，包含已完成项和当前上传后验证失败的项；具体错误写入标准错误。调用方必须同时检查退出码和 `verification`，不能仅凭有 URL 就视为成功，也不要整批自动重传。未取得 URL 的失败项不会出现在结果数组中。

## 验证原则

不要只信任响应头。某些服务会返回类似：

```text
image/webp;text/html; charset=utf-8
```

脚本会下载响应体，通过文件魔数识别 PNG、JPEG、GIF、WebP、BMP、TIFF、ICO、AVIF 或 SVG。栅格图片使用 Pillow 检查结构后重新打开并解码所有帧，拒绝只有文件头或像素数据损坏的文件；同时补齐 TIFF 等格式的尺寸。若下载结果是 HTML、未知内容、损坏图片，或当前 Pillow 构建无法解码该格式，验证失败。

解码验证覆盖 Pillow 暴露的图像帧；ICO 按默认尺寸验证，不逐个检查内嵌的其他尺寸条目。

SVG 使用 XML 解析器检查文档结构和根元素；这不等同于浏览器渲染验收，不检查脚本、字体或外链资源能否加载。项目要求的格式、尺寸和实际展示效果仍由调用方核对。

服务端可能把上传的 PNG 自动转成 WebP；只要远程文件是有效图片且满足调用方要求，这不等同于上传失败。

## SVG 转换

默认上传原始 SVG。只有显式传入 `--convert-svg` 时才调用 `rsvg-convert` 生成 PNG。

- 按本技能工作流转换时显式提供 `--width` 与 `--height`。脚本也支持同时省略这两个参数，按 SVG 自身尺寸导出；不能只提供其中一个。
- 转换文件默认写入 `$TMP_DIR/easyimage-upload/`；未设置 `TMP_DIR` 时使用系统临时目录。
- `--keep-existing` 可以复用同名转换文件。

## 常见错误

- `PicGo config not found`：用 `--config` 指定配置，或创建 `~/.picgo/config.json`。
- `Missing picBed.web-uploader`：检查 PicGo 当前上传器配置。
- `Invalid customHeader/customBody JSON`：字段必须是对象或能解析为对象的 JSON 字符串。
- `Required tool not found: curl`：安装 curl。
- `Required Python package not found: Pillow`：在运行脚本的 Python 环境安装 Pillow；脚本会在上传前检查此依赖。仅检查配置不需要它。
- `Required tool not found: rsvg-convert`：仅 SVG 转换需要；macOS 可通过 `brew install librsvg` 安装。
- `Upload response is not valid JSON`：检查上传地址、认证字段和服务端错误页。
- `Remote response is not a recognized image`：正式 URL 返回了 HTML、鉴权错误或不支持的文件格式。
- `Remote image content is invalid or unsupported by this Pillow build`：文件结构或像素数据损坏，或当前 Pillow 缺少对应格式的解码支持；保留已上传 URL，核对服务端响应后再决定是否重传。
