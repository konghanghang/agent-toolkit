# PicGo Web Uploader 配置

## 安装 PicGo-Core 与插件

已有可用的 `~/.picgo/config.json` 时可以跳过安装。

```bash
npm install picgo -g
picgo install web-uploader
```

插件安装完成后，可以通过交互命令创建配置：

```bash
picgo set uploader
```

选择 `web-uploader`，依次填写 EasyImage API 地址、文件参数名、URL JSON 路径、请求头和 Body。

## 配置文件位置

- Linux、macOS：`~/.picgo/config.json`
- Windows：`C:\Users\<username>\.picgo\config.json`

本 skill 默认读取当前用户主目录下的 `.picgo/config.json`，也可以通过 `--config` 指定其他文件。

## 最小配置示例

不要复制真实 Token 到仓库、聊天记录或公开文档。

```json
{
  "picBed": {
    "current": "web-uploader",
    "web-uploader": {
      "url": "https://images.example.com/api/index.php",
      "paramName": "image",
      "jsonPath": "url",
      "customHeader": "{}",
      "customBody": "{\"token\":\"YOUR_EASYIMAGE_TOKEN\"}"
    }
  }
}
```

脚本只要求存在 `picBed.web-uploader`；`picBed.current` 主要供 PicGo-Core 自身选择默认上传器。

## 字段说明

| 字段 | 作用 | 常见值 |
|---|---|---|
| `url` | EasyImage API 上传地址 | `https://images.example.com/api/index.php` |
| `paramName` | multipart 文件字段名 | `image` |
| `jsonPath` | 响应 JSON 中正式图片 URL 的路径 | `url` 或 `data.url` |
| `customHeader` | 额外请求头 | `{}` |
| `customBody` | 额外表单字段，通常包含上传 Token | `{"token":"YOUR_EASYIMAGE_TOKEN"}` |

`customHeader` 和 `customBody` 可以是 JSON 对象，也可以是内容能解析成对象的 JSON 字符串。

## EasyImage 侧准备

在 EasyImage 管理后台确认：

1. 已启用 API 上传。
2. 已生成可用的上传 Token。
3. API 地址与部署域名一致。
4. 服务端允许当前文件类型和文件大小。

不要把后台 Token 写入 `SKILL.md`、脚本参数示例或 Git 仓库。

## 检查配置

```bash
python3 "$AGENT_TOOLKIT_ROOT/skills/upload-to-easyimage/scripts/upload_easyimage.py" \
  --check-config
```

输出会展示：

- 配置文件路径
- 上传接口地址
- 文件字段名
- URL JSON 路径
- `customHeader` 的键名
- `customBody` 的键名

不会展示 Header 和 Body 的具体值。如果输出包含 `token`，只表示存在这个字段，不代表 Token 被打印。

## 延伸阅读

- [Typora 使用 PicGo-Core 配置上传图片到 EasyImage 图床](https://www.iminling.com/2025/typora-how-to-use-picgo-upload-image-to-easyimage/)
- [EasyImages2.0：使用 PicGo 上传](https://github.com/icret/EasyImages2.0/blob/master/docs/%E4%BD%BF%E7%94%A8PicGo%E4%B8%8A%E4%BC%A0.md)

线上文章提供完整图文配置过程；本文件保留 skill 正常运行所需的最小配置，避免把外部页面作为唯一依赖。
