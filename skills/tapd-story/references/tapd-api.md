# TAPD API 与认证

## 配置

认证优先级：

1. `TAPD_TOKEN`，使用 Bearer Auth。
2. `TAPD_API_USER` + `TAPD_API_PASSWORD`，使用 Basic Auth。

其他配置：

- `TAPD_WORKSPACE_ID`：默认工作区；代码不写死工作区。
- `TAPD_BASE_URL`：默认 `https://api.tapd.cn`。
- `WORK_ROOT`：扩大仓库检索时的工作区根目录。

Token、账号、密码、Cookie 和 Session 不得写入 Skill 或规格文件。

## 需求接口

- 读取需求：`GET /stories`
- 创建需求：`POST /stories`
- 更新需求：`POST /stories`，必须传 `id`

读取重点字段：

```text
id,name,description,status,status_label,priority,priority_label,
owner,creator,developer,version,iteration_id,created,modified
```

## 图片接口

需求 HTML 中的图片路径通过下面的接口换取临时下载地址：

```text
GET /files/get_image
```

参数：

- `workspace_id`
- `image_path`

返回 `Attachment.download_url`，有效时间通常为 300 秒。下载后必须检查图片签名，不能只相信扩展名或响应头。

官方文档：

- `open.tapd.cn/document/api-doc/API文档/api_reference/story/get_stories`
- `open.tapd.cn/document/api-doc/API文档/api_reference/attachment/get_image`
