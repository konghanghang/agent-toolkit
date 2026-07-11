---
name: upload-to-easyimage
description: 通过本机 PicGo `web-uploader` 配置将一个或多个本地图片上传到 EasyImage，返回正式图片与缩略图地址，并下载远程响应验证真实格式、尺寸和文件内容。适用于“上传到 EasyImage”“上传图片并返回链接”“批量上传图片”“检查 EasyImage 图片是否有效”或显式将 SVG 转为 PNG 后上传等任务；不负责生成图片、修改项目文件或执行发布流程。
---

# 上传图片到 EasyImage

使用 [scripts/upload_easyimage.py](scripts/upload_easyimage.py) 完成配置读取、上传和远程图片验证。首次配置先读 [references/picgo-config.md](references/picgo-config.md)，上传结果、验证字段与故障处理见 [references/workflow.md](references/workflow.md)。

## 边界

- 只处理图片上传、可选 SVG 转 PNG，以及返回结果验证。
- 默认保持原始文件、格式和尺寸，不修改输入文件。
- 不生成或编辑图片，不修改 Markdown、Front Matter、配置文件或项目代码。
- 不执行构建、发布、提交或推送；上传后的 URL 如何使用由当前项目规则决定。
- 上传是外部状态变更。只有用户明确要求上传时才执行；只要求检查配置或命令时不要上传。

## 工作流

1. 检查输入文件存在，确认不是凭证、截图中的敏感信息或无关文件。
2. 阅读当前项目规则，确认是否要求特定格式、尺寸或文件命名；项目规则优先于本 skill 的通用默认值。
3. 首次使用或配置异常时，读取 `references/picgo-config.md`，再运行 `--check-config`。输出只展示配置路径、接口地址和字段名，不展示 Header、Body 的值。
4. 默认直接上传原图。仅当用户或项目规则明确要求时，使用 `--convert-svg` 将 SVG 转成 PNG；尺寸参数必须显式提供。
5. 保持远程验证开启。脚本会下载正式 URL，依据文件内容识别真实格式和尺寸，不只依赖可能异常的 `Content-Type`。
6. 向用户报告每个输入文件的正式 URL、缩略图 URL、远程真实格式、尺寸，以及是否执行了 SVG 转换。

## 命令

检查本机配置：

```bash
python3 "$SKILLS_ROOT/upload-to-easyimage/scripts/upload_easyimage.py" \
  --check-config
```

上传单张或多张原图：

```bash
python3 "$SKILLS_ROOT/upload-to-easyimage/scripts/upload_easyimage.py" \
  image.png screenshot.webp
```

显式将 SVG 转成指定尺寸 PNG 后上传：

```bash
python3 "$SKILLS_ROOT/upload-to-easyimage/scripts/upload_easyimage.py" \
  cover.svg \
  --convert-svg \
  --width 1200 \
  --height 630
```

使用其他 PicGo 配置文件：

```bash
python3 "$SKILLS_ROOT/upload-to-easyimage/scripts/upload_easyimage.py" \
  image.png \
  --config /path/to/picgo-config.json
```

## SVG 规则

EasyImage 可以接受 SVG，但部分 `hide.php` 配置可能返回异常媒体类型，导致浏览器下载而不是直接显示。不要擅自转换；当用户或项目规则要求稳定的网页显示时，使用 `--convert-svg`。需要 `rsvg-convert`，且转换文件写入 `--output-dir` 或 `TMP_DIR/easyimage-upload/`。

## 验证与安全

- 保留上传响应中的 `easyimage_url`、`thumb` 和 `src_name`。
- 远程验证失败、下载到 HTML、真实格式无法识别或尺寸不符合项目要求时，不把 URL 宣称为可用成品。
- 不输出 `customHeader`、`customBody` 的值，不在日志或回复中泄露 Token、Cookie 或其他凭证。
- 不因上传成功而删除本地源文件或转换文件。
- `--no-verify` 只在用户明确接受无法验证的风险或远程下载确实不可用时使用，并在结果中说明。
