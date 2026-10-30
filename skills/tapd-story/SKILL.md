---
name: tapd-story
description: 读取和管理 TAPD 需求。用于按需求 ID 获取原始内容、图片和附件、结合代码仓库整理实施上下文、生成结构化 HTML 需求草稿，以及在用户明确要求时创建或更新单条需求、父需求和子任务。单独需求管理完成当前操作后结束，不默认进入开发交付或知识库建档。
---

# TAPD 需求管理

## 核心边界

- 把 TAPD 内容视为需求事实，把仓库代码和当前目录适用的 `AGENTS.md` 视为实现事实。
- “查看、分析、给方案、整理草稿”均为只读或本地生成操作，不得写入 TAPD。
- 只有用户明确要求“创建、同步、更新 TAPD”时，才执行 `create` 或 `update`。
- 创建或更新 TAPD、本地建档与业务开发分别判断授权；认可草稿或澄清完成不新增写入或开发授权。取得 TAPD 编号、确认处理人或指派给用户本人，也不自动触发本地建档或开发。
- 单独需求管理以用户要求的草稿、分析或 TAPD 操作为终点。创建或更新成功后回读核验并返回 ID、链接、处理人及必要待确认项，即完成本轮任务；草稿与中间材料保留在对话或临时目录。只有本轮明确要求本地记录维护，或已授权进入业务开发交付时，才按 [工作区衔接](references/work-context.md) 继续；不默认生成开发、测试或发布清单。
- 图片中清晰可辨的内容属于需求事实；识别不清、内容截断或存在歧义的部分必须标记为待确认，不得猜测。
- TAPD 正文是需求事实。附件是客户原始材料，产品通常已整理进正文；正文足够时不要打开附件。正文缺失、含糊、明确要求参照附件、已有证据表明内容冲突，或用户要求读取附件时再按需获取。冲突和不确定内容标为待确认。

## 环境变量

- `TAPD_TOKEN`：必填，作为 Bearer Token 调用 TAPD API。
- `TAPD_WORKSPACE_ID`：默认工作区；也可以通过命令参数或 JSON 规格临时覆盖。
- `WORK_ROOT`：可选，仅在扩大代码检索范围时使用。

## 先恢复已有任务

收到需求 ID 时，先按项目约定查找已有任务记录，读取阶段、已确认口径、分析材料和下一步；有实现证据时按需核对对应仓库状态，再判断是继续开发、验证、已完成后的追加调整还是新分析。没有记录不等于没有实现；已有记录可按需读取，其存在不扩大本轮写入范围。工作区按 [任务恢复顺序](../work-feature-delivery/references/delivery-workflow.md#11-先恢复任务再补需求与知识) 执行；仅恢复上下文不启动未授权的后续交付动作。

没有需求正文、需要刷新最新内容、记录与当前要求冲突或用户明确要求远端原文时，再执行 `detail` / `plan-context`。已有资料足够时复用，不为给出 ID 自动重拉 TAPD。明确要求查看最新 TAPD 时，在本地记录检查后按要求刷新，不能用旧记录代替。

无 ID 的 work 讨论、新业务需求或独立技术任务，先按 [工作区衔接](references/work-context.md) 完成必要探索、澄清和草稿；确需新建且已有明确创建授权时才进入 `create`，不以虚构 ID 调用读取接口。

## Agent 编排

- 主 Agent 先恢复本地任务、判断是否需要远端读取，再控制 TAPD 写入授权、启动必要任务和汇总结论；本轮包含交付记录维护时，按项目规则将确认事实与有效代码结论分类落盘。
- 当前环境支持 subagent 时，默认使用全新或最小上下文的 TAPD Worker 执行需求读取、图片识别和草稿整理；主 Agent 不要先读取 TAPD 再决定是否委派。
- TAPD Worker 将完整结果写入临时目录，只返回结果文件路径、简短摘要和待确认项，避免把原始 HTML、图片识别过程、附件正文和大段中间结果带回主上下文。
- 需要探索代码时，启动独立的只读 Code Explorer；按 `references/agent-orchestration.md` 判断与 TAPD 读取并行还是串行，并按 `references/read-and-plan.md` 从足以回答当前问题的最小范围开始逐步扩大。
- 需要创建或更新时，使用独立的 TAPD Writer 读取已确认规格；只有主 Agent 已确认用户明确授权后，才能执行真实写入。
- 当前环境不支持 subagent 时，由主 Agent 按相同边界直接执行。

## 模式选择

1. 查看需求：完成任务恢复检查，确需远端读取时运行 `detail`，优先读取标题、原始描述、图片、附件清单和补充字段。
2. 形成实施计划：复用已有记录，需补远端需求时运行 `plan-context`，结合需求事实、代码证据和目标仓库规则分析影响范围。外部行为、状态语义、兼容性或多仓范围尚有歧义时使用 `grilling`；有必要记录稳定术语或架构决策时使用 `domain-modeling`。只要求方案时，交付方案后结束；用户已要求实施且范围明确时，按目标项目流程继续已授权工作，不重复确认。工作区的衔接见 [references/work-context.md](references/work-context.md)。
3. 整理需求草稿：默认面向产品、开发和测试共同阅读；按业务复杂度生成简洁 JSON 规格，并运行 `validate-spec`，不要调用写接口。
4. 创建需求：用户明确授权后，核对已确认的处理人；未指定时主动询问，只有用户明确要求不指定处理人时才允许留空。运行 `create --dry-run` 并核对真实规格后再执行 `create`；已有明确创建授权和必要字段时不重复索要开工确认。创建后回读核验标题、处理人和正文，保留真实 ID、链接与实际结果，按核心边界和本轮授权交付；本轮另含本地记录维护或业务开发交付时，再按工作区衔接继续。
5. 更新需求：用户明确授权后，先运行 `update --dry-run`，核对后再运行 `update`；回读核验实际变更字段，按核心边界和本轮授权交付。

## 只读命令

读取需求：

```bash
python3 scripts/tapd_story.py detail --id STORY_ID
```

读取正文、下载图片并列出附件清单：

```bash
python3 scripts/tapd_story.py detail --id STORY_ID --download-images
```

正文缺失、含糊、明确要求参照附件，或用户要求读取附件时，按清单中的 ID 下载（可重复指定）：

```bash
python3 scripts/tapd_story.py detail --id STORY_ID --attachment-id ATTACHMENT_ID
```

确需全部附件时使用 `--download-attachments`，不能与 `--attachment-id` 混用。默认不下载附件。

生成仓库实施上下文：

```bash
python3 scripts/tapd_story.py plan-context --id STORY_ID --repo . --download-images
```

Windows PowerShell 使用工具包中的绝对脚本路径，不依赖当前目录：

```powershell
$AgentToolkitRoot = (Resolve-Path (Join-Path $HOME ".agent-toolkit")).Path
$PythonBin = (Get-Command python).Source
& "$PythonBin" "$AgentToolkitRoot\skills\tapd-story\scripts\tapd_story.py" detail --id STORY_ID --download-images
```

图片下载后，使用当前 Agent 可用的视觉/OCR 能力读取本地文件；在最终输出中保留图片顺序和正文上下文。清晰内容纳入确认需求事实，不确定部分单独标记为待确认。图片或附件失败不得阻塞文字需求分析。不要把附件全文复制进需求正文或最终答复。

## 写入命令

先使用 `Path(tempfile.gettempdir()) / "tapd-story.json"` 或同等平台 API 生成临时规格文件路径，并在下列命令中用实际路径替换 `<SPEC_FILE>`。不要把占位符原样传给命令，也不要把临时规格写入当前项目目录。

先校验和预览：

```bash
python3 scripts/tapd_story.py validate-spec --operation create --spec "<SPEC_FILE>"
python3 scripts/tapd_story.py create --spec "<SPEC_FILE>" --dry-run
```

用户明确确认后创建：

```bash
python3 scripts/tapd_story.py create --spec "<SPEC_FILE>"
```

更新已有需求：

```bash
python3 scripts/tapd_story.py validate-spec --operation update --spec "<SPEC_FILE>"
python3 scripts/tapd_story.py update --spec "<SPEC_FILE>" --dry-run
python3 scripts/tapd_story.py update --spec "<SPEC_FILE>"
```

禁止隐式 upsert：`create` 规格不得包含 `id`，`update` 的每个条目必须包含 `id`。

## 标题规则

- 新建客户接口自定义开发需求时，标题固定使用 `【接口自定义】具体需求 - 具体企业名`。
- `具体需求` 必须描述本次实际业务改动，不使用“接口调整”“客户需求”等空泛表述；`具体企业名` 使用客户或企业的明确名称。
- 缺陷修复 / 口径修正类需求，标题优先问题导向，例如：`【接口优化】修正xxx误记为yyy`。
- 在草稿阶段就按适用规则生成标题；需要企业名的客户自定义需求无法从用户描述或已确认事实确定企业时，创建前必须询问，不得猜测。
- 独立性能优化、重构等技术任务按已确认目标和范围命名，不套用客户接口自定义标题模板，也不虚构企业名。
- 查看或更新已有需求时默认保留原始标题；只有用户明确要求调整标题时，才按上述规则修改。

## 需求草稿原则

生成或改写 `description_html` 时，遵守 `references/html-description.md`：

- 正文以最短但足够理解和判断完成为准，不固定章节数量，不为凑模板创建空章
- 普通单需求以 `需求说明` 为核心；`背景`、`影响范围`、`验收标准` 仅在能够补充必要信息时添加
- 需求说明已经明确预期结果时，不单独重复验收标准；存在多路径、边界、兼容或口径差异时，再补充验收条件
- 大章使用 `h3`；明细直接使用短段落、列表或表格，避免层级过深和长段复述
- 只有存在多路径、多状态或多角色差异时才使用对比表或场景表
- 用户只要草稿时，不写类名、方法名、调用链等实施细节

## 输出要求

- 保持 TAPD 原标题，不擅自改写。
- 清理 HTML 时保持原始段落和列表顺序。
- 严格区分需求草稿与实施计划：用户只要求需求草稿时，直接写清要改变的业务行为和预期结果，不自动展开成技术方案。
- 需求草稿默认面向产品、开发和测试共同阅读；除非影响外部行为或用户明确要求，否则不写类名、方法名、注解、配置项、框架处理和异常实现等内部细节。
- 接口类需求保留必要的外部契约，如接口地址、调用方式、必填参数和响应行为；内部调用链和实现方式放到实施计划或代码上下文中。
- 明确分开：确认需求事实、图片中的不确定内容、仅在正文不足时从附件确认的事实、代码证据、推断路径、待确认项。
- 创建或更新后返回需求 ID、链接、owner 是否设置和执行模式。仅在本轮另已授权本地建档且建档失败时，保留真实 ID 并分别报告 TAPD 成功与本地建档失败，恢复时复用该 ID，不重复创建；未取得真实 ID 时不伪造编号或冒用 Fix 继续开发。
- 父需求成功而子任务失败时，明确输出部分成功、失败项和未执行项，不宣称已回滚。

## 按需读取

- Subagent 职责、交接文件和上下文隔离：`references/agent-orchestration.md`
- 读取、图片、附件和实施上下文：`references/read-and-plan.md`
- 创建、更新和授权边界：`references/create-and-update.md`
- JSON 规格：`references/spec-schema.md`
- HTML 描述规范：`references/html-description.md`
- TAPD API、认证、图片和附件接口：`references/tapd-api.md`
- 编码前澄清边界：`grilling`
- 稳定术语和必要架构决策：`domain-modeling`
- 工作区交付衔接与知识目录：`references/work-context.md`
