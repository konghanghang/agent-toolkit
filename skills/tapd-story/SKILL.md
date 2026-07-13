---
name: tapd-story
description: 读取和管理 TAPD 需求。用于按需求 ID 获取原始内容与图片、结合代码仓库整理实施上下文、生成结构化 HTML 需求草稿，以及在用户明确要求时创建或更新单条需求、父需求和子任务。
---

# TAPD 需求管理

## 核心边界

- 把 TAPD 内容视为需求事实，把仓库代码和当前目录适用的 `AGENTS.md` 视为实现事实。
- “查看、分析、给方案、整理草稿”均为只读或本地生成操作，不得写入 TAPD。
- 只有用户明确要求“创建、同步、更新 TAPD”时，才执行 `create` 或 `update`。
- 图片中清晰可辨的内容属于需求事实；识别不清、内容截断或存在歧义的部分必须标记为待确认，不得猜测。

## 环境变量

- `TAPD_TOKEN`：必填，作为 Bearer Token 调用 TAPD API。
- `TAPD_WORKSPACE_ID`：默认工作区；也可以通过命令参数或 JSON 规格临时覆盖。
- `WORK_ROOT`：可选，仅在扩大代码检索范围时使用。

## 模式选择

1. 查看需求：运行 `detail`，优先读取标题、原始描述、图片和补充字段。
2. 形成实施计划：运行 `plan-context`，再结合仓库和当前 `AGENTS.md` 编写最终方案。
3. 整理需求草稿：生成 JSON 规格并运行 `validate-spec`，不要调用写接口。
4. 创建需求：用户明确授权后，先运行 `create --dry-run`，核对后再运行 `create`。
5. 更新需求：用户明确授权后，先运行 `update --dry-run`，核对后再运行 `update`。

## 只读命令

读取需求：

```bash
python3 scripts/tapd_story.py detail --id STORY_ID
```

下载需求图片供视觉识别：

```bash
python3 scripts/tapd_story.py detail --id STORY_ID --download-images
```

生成仓库实施上下文：

```bash
python3 scripts/tapd_story.py plan-context --id STORY_ID --repo . --download-images
```

图片下载后，使用当前 Agent 可用的视觉/OCR 能力读取本地文件；在最终输出中保留图片顺序和正文上下文。清晰内容纳入确认需求事实，不确定部分单独标记为待确认。图片失败不得阻塞文字需求分析。

## 写入命令

先校验和预览：

```bash
python3 scripts/tapd_story.py validate-spec --operation create --spec /tmp/tapd-story.json
python3 scripts/tapd_story.py create --spec /tmp/tapd-story.json --dry-run
```

用户明确确认后创建：

```bash
python3 scripts/tapd_story.py create --spec /tmp/tapd-story.json
```

更新已有需求：

```bash
python3 scripts/tapd_story.py validate-spec --operation update --spec /tmp/tapd-story.json
python3 scripts/tapd_story.py update --spec /tmp/tapd-story.json --dry-run
python3 scripts/tapd_story.py update --spec /tmp/tapd-story.json
```

禁止隐式 upsert：`create` 规格不得包含 `id`，`update` 的每个条目必须包含 `id`。

## 输出要求

- 保持 TAPD 原标题，不擅自改写。
- 清理 HTML 时保持原始段落和列表顺序。
- 明确分开：确认需求事实、图片中的不确定内容、代码证据、推断路径、待确认项。
- 创建或更新后返回需求 ID、链接、owner 是否设置和执行模式。
- 父需求成功而子任务失败时，明确输出部分成功、失败项和未执行项，不宣称已回滚。

## 按需读取

- 读取、图片和实施上下文：`references/read-and-plan.md`
- 创建、更新和授权边界：`references/create-and-update.md`
- JSON 规格：`references/spec-schema.md`
- HTML 描述规范：`references/html-description.md`
- TAPD API、认证和图片接口：`references/tapd-api.md`
