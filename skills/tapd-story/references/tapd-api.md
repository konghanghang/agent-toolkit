# TAPD API 与认证

## 配置

认证只使用 `TAPD_TOKEN`，并通过 Bearer Auth 调用 TAPD API。

其他配置：

- `TAPD_WORKSPACE_ID`：默认工作区；代码不写死工作区，也不提供固定默认值。
- `WORK_ROOT`：扩大仓库检索时的工作区根目录。

`TAPD_TOKEN`、Cookie 和 Session 不得写入 Skill 或规格文件。API 地址固定为 `https://api.tapd.cn`，仅允许通过 `--base-url` 临时覆盖调试。

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
