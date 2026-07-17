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

## Agent 编排

- 主 Agent 只负责识别用户意图、控制 TAPD 写入授权、启动后续任务和汇总最终结果。
- 当前环境支持 subagent 时，默认使用全新或最小上下文的 TAPD Worker 执行需求读取、图片识别和草稿整理；主 Agent 不要先读取 TAPD 再决定是否委派。
- TAPD Worker 将完整结果写入临时目录，只返回结果文件路径、简短摘要和待确认项，避免把原始 HTML、图片识别过程和大段中间结果带回主上下文。
- 需要探索代码时，启动独立的只读 Code Explorer，只传递结构化需求文件路径和目标仓库路径；跨仓库时按仓库并行探索。
- 需要创建或更新时，使用独立的 TAPD Writer 读取已确认规格；只有主 Agent 已确认用户明确授权后，才能执行真实写入。
- 当前环境不支持 subagent 时，由主 Agent 按相同边界直接执行。

## 模式选择

1. 查看需求：运行 `detail`，优先读取标题、原始描述、图片和补充字段。
2. 形成实施计划：运行 `plan-context`，再结合仓库和当前 `AGENTS.md` 编写最终方案。
3. 整理需求草稿：默认面向产品、开发和测试共同阅读，按业务层级生成简洁的 JSON 规格并运行 `validate-spec`，不要调用写接口。
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

## 标题规则

- 新建客户接口自定义开发需求时，标题固定使用 `【接口自定义】具体需求 - 具体企业名`。
- `具体需求` 必须描述本次实际业务改动，不使用“接口调整”“客户需求”等空泛表述；`具体企业名` 使用客户或企业的明确名称。
- 在草稿阶段就按该规则生成标题；企业名无法从用户描述或已确认事实中确定时，创建前必须询问，不得猜测。
- 查看或更新已有需求时默认保留原始标题；只有用户明确要求调整标题时，才按上述规则修改。

## 输出要求

- 保持 TAPD 原标题，不擅自改写。
- 清理 HTML 时保持原始段落和列表顺序。
- 严格区分需求草稿与实施计划：用户只要求需求草稿时，优先写业务背景、目标、用户可见行为、范围边界和验收标准，不自动展开成技术方案。
- 需求草稿默认面向产品、开发和测试共同阅读；除非影响外部行为或用户明确要求，否则不写类名、方法名、注解、配置项、框架处理和异常实现等内部细节。
- 接口类需求保留必要的外部契约，如接口地址、调用方式、必填参数和响应行为；内部调用链和实现方式放到实施计划或代码上下文中。
- 明确分开：确认需求事实、图片中的不确定内容、代码证据、推断路径、待确认项。
- 创建或更新后返回需求 ID、链接、owner 是否设置和执行模式。
- 父需求成功而子任务失败时，明确输出部分成功、失败项和未执行项，不宣称已回滚。

## 按需读取

- Subagent 职责、交接文件和上下文隔离：`references/agent-orchestration.md`
- 读取、图片和实施上下文：`references/read-and-plan.md`
- 创建、更新和授权边界：`references/create-and-update.md`
- JSON 规格：`references/spec-schema.md`
- HTML 描述规范：`references/html-description.md`
- TAPD API、认证和图片接口：`references/tapd-api.md`
