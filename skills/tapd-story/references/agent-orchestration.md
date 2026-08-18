# Agent 编排与上下文交接

## 职责边界

- 主 Agent：判断用户目标、确认写入授权、选择目标仓库、启动 Worker、汇总最终结果。
- TAPD Worker：读取需求、清理 HTML、下载图片、完成视觉识别和生成草稿；生成草稿时遵守 `html-description.md`，按需求复杂度保持简洁，不固定章节数量或强制单列验收标准。
- Code Explorer：只读探索代码，从能够回答当前核心问题的最小范围开始，按需确认会改变结论的实现事实。
- TAPD Writer：读取已确认规格，执行校验和 dry-run，并在明确授权后执行创建或更新。

当前环境支持 subagent 时，使用全新或最小上下文启动 Worker，不继承完整对话。只传递当前操作、需求 ID、目标仓库、输入输出文件路径和写入授权状态。

TAPD 读取和代码探索不固定为串行流程：

- 两类事实可以独立获取时并行执行，最终由主 Agent 汇合需求事实和代码事实。
- 用户已提供明确代码入口时，Code Explorer 可以先读取目标代码，不必等待完整需求上下文。
- 代码探索必须依赖需求字段、图片或验收口径时，先完成 TAPD Worker，再启动 Code Explorer。
- 并行只用于缩短必要事实的获取时间，不得借此扩大探索范围。

## 交接目录

按需求使用 `Path(tempfile.gettempdir()) / f"tapd-story-{story_id}"` 作为交接目录。下文的 `<TAPD_TEMP>` 仅表示这个实际路径，使用前必须替换，不要原样传给命令：

```text
<TAPD_TEMP>/
├── detail.json
├── images/
├── requirement-context.json
├── requirement-context.md
├── draft-spec.json
└── code-context.md
```

创建新需求且没有需求 ID 时，使用能够区分本次操作的临时目录。默认不要持久化原始 TAPD 响应；只有字段排查确实需要时才使用 `--raw`，并禁止把其中的敏感内容带入最终输出。

## TAPD Worker 输出

先将读取结果写入文件，避免把完整内容直接作为 subagent 返回值：

```bash
python3 scripts/tapd_story.py detail \
  --id STORY_ID \
  --download-images \
  --format json \
  --image-dir "<TAPD_TEMP>/images" \
  --output "<TAPD_TEMP>/detail.json"
```

完成图片识别后生成 `requirement-context.json` 和 `requirement-context.md`。结构化结果至少包含：

```json
{
  "story_id": "STORY_ID",
  "title": "TAPD 原标题",
  "confirmed_facts": [
    {
      "content": "确认的需求事实",
      "source": "description"
    },
    {
      "content": "图片中清晰可辨的事实",
      "source": "image:1"
    }
  ],
  "acceptance_criteria": [],
  "field_codes": [],
  "urls": [],
  "uncertain_items": [],
  "images": [
    {
      "index": 1,
      "local_path": "<TAPD_TEMP>/images/001.png"
    }
  ]
}
```

TAPD Worker、Code Explorer 或 TAPD Writer 返回主 Agent 时只报告：

- 操作是否完成。
- 需求 ID 和原始标题。
- 图片数量和识别状态。
- 确认事实与待确认项数量。
- 结果文件路径。
- 涉及写操作时，报告 dry-run 或真实执行状态。

用户明确要求查看完整需求时，将规范化后的可读结果写入 `requirement-context.md`，由主 Agent 按需读取；不要返回原始 HTML 和图片识别过程。

## Code Explorer 输入与输出

根据当前任务，只向 Code Explorer 传递必要输入：

- 已生成且当前探索需要的 `requirement-context.json` 或 `requirement-context.md` 路径。
- 目标仓库路径和用户明确提供的代码入口。
- 当前目录适用的 `AGENTS.md` 读取要求。

Code Explorer 必须保持只读，并将结果写入 `code-context.md`。探索深度遵守 `read-and-plan.md` 的渐进式实施上下文规则，输出至少包含：

1. 当前核心问题的代码结论。
2. 带文件路径和行号的必要代码证据。
3. 建议修改点、影响范围和待确认项。

只有确实会改变当前结论或属于用户要求的交付内容时，才补充完整调用链、跨项目影响、发布服务、测试边界和兼容性风险。

候选搜索结果不能直接作为最终修改范围。代码推断不能写成已确认需求事实。

## TAPD Writer 与写入授权

- 草稿整理、规格校验和 dry-run 不代表已经获得真实写入授权。
- 主 Agent 只有在用户明确要求创建、同步或更新 TAPD 后，才能启动真实写入。
- TAPD Writer 只读取已经确认的规格文件，不从原始 TAPD 内容重新推断写入字段。
- 创建和更新仍遵守禁止隐式 upsert、父子任务部分成功报告和敏感内容脱敏规则。
