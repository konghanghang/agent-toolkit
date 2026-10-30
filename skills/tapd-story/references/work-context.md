# 工作区衔接

仅在任务属于 工作区时读取；通用需求分析、澄清和领域建模不依赖本文件中的路径。

## 交付流程

单独需求管理的范围与完成条件见 [核心边界](../SKILL.md#核心边界)。只有本轮明确要求本地记录维护，或已有业务开发交付授权时，才继续以下衔接。

收到需求 ID 时，先按 [任务恢复顺序](../../work-feature-delivery/references/delivery-workflow.md#11-先恢复任务再补需求与知识) 查找本地主记录，再决定是否读取远端。已有需求的探索和澄清同样按 [交付记录](../../work-feature-delivery/references/delivery-record.md) 及时保存结论，不要求进入编码才记录。

工作项目业务功能、Fix 及独立性能优化、重构等技术交付，由 [work-feature-delivery](../../work-feature-delivery/SKILL.md) 自动复用通用流程并叠加团队交付要求；先按 [阶段衔接](../../work-feature-delivery/references/stages.md) 识别需求 ID、讨论、故障、继续任务或已有成果入口，从真实阶段接续。只看需求、澄清、专项审查或记录术语时，完成对应目标，不重走完整开发。

交付规则统一维护在 [work-feature-delivery 的交付规则](../../work-feature-delivery/references/delivery-workflow.md)。先读取目标仓库适用的协作规则，再按任务阶段读取对应章节；不再依赖 docs 下的旧 workflow。

- 影响分析按第 1 节确认仓库、模块、服务和验证点，以代码、配置或用户明确说明为依据。
- 未决业务语义由 `grilling` 澄清；release 基线确认仍按第 2 节处理。
- 无 ID 的讨论先收敛范围、排除项、验收和待验证事项；新业务需求与独立技术交付复用已有 TAPD，需新建时按交付规则的需求建立阶段，经明确创建授权取得真实 ID；是否建立本地主记录按 [何时落盘](../../work-feature-delivery/references/delivery-record.md#何时落盘) 判断。只获创建授权时按需求管理的完成条件结束；创建与实施均已授权且前置满足时连续推进。
- 原需求内必要技术调整复用原 ID；仅已确认的无 TAPD Bug Fix 使用知识库既有 Fix 编号。创建失败或缺授权时只阻塞依赖该步骤的动作，可继续独立只读调查；已有真实 ID 时恢复它，不重复创建。
- 只要求分析或方案时，交付结果后结束。已要求实施且范围明确时，按第 2 节及后续阶段继续已授权工作，不重复确认已有口径。
- 提交、推送、合并、部署及数据库执行按已有授权分别处理；业务确认不扩大操作授权。
- 保留当前仓库开发、基线与发布批次独立判断、按需求提测和按批次上线的约定，不引入默认 worktree 或 amend。

## 知识文档

知识库位于 `~/data/work/docs/`。写入前读取该目录适用的协作规则，沿用现有文件及模板：

| 内容 | 目录 |
|---|---|
| 后续需要复用的稳定业务术语 | `knowledge/<业务域>/`，使用 `docType: glossary` 并遵守 `templates/knowledge.md` frontmatter |
| 现行系统边界和已实施架构事实 | `knowledge/<业务域>/`，使用 `docType: system` |
| 尚未落地的跨仓库方案决策 | 对应 `delivery/requirements/<ID>/index.md`；跨需求专项按需使用 `delivery/initiatives/` |

临时叫法留在对话；本轮已授权保存的需求确认事实按知识库分类规则写入主记录及必要的分析附件。未实施方案不写成长期知识；用户未要求记录长期知识时，不为完成流程而建文件。不要把术语或 ADR 复制到提测、上线清单。修改、提交和推送遵守对应仓库规则与已有授权。
