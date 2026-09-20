---
name: domain-modeling
description: 在澄清过程中维护已确认的业务术语，并在确有必要时记录架构决策。适用于 grilling 问清口径后需要把稳定术语或难逆决策留下来，或其他技能需要维护领域模型时；不要把实现细节写成术语。
---

# Domain Modeling

在设计过程中主动打磨领域语言。这是 *active* discipline：挑战术语、用具体场景压边界，并在概念成形且用户确认后写入知识库。仅仅读取已有术语来使用它们，不是这个 skill。

本技能默认由 `grilling` 在澄清过程中按需调用。

## 落点

先读取目标项目的协作规则和现有知识文档，沿用其术语表、架构决策或计划目录及格式，不强制新建统一目录。

- 临时叫法留在对话中。
- 已确认且以后仍会复用的术语，按需写入现有术语文档；单项目稳定术语同样适用。
- 必要架构决策沿用项目已有 ADR 或设计文档位置，明确区分已实施事实与未实施方案。
- 未约定落点且用户未要求落盘时，在对话中整理即可。

work 工作区的目录映射见 [work 工作区约定](../tapd-story/references/work-context.md)，其他项目不套用这套路径。

写入知识库前先说明要记什么、写到哪篇、为什么现在就要落盘；用户未要求记录长期知识时，不要为了走完流程而建文件。修改知识库仍须遵守该仓库协作规则；提交和推送另行授权。

术语定义格式见 [CONTEXT-FORMAT.md](CONTEXT-FORMAT.md)。ADR 正文格式见 [ADR-FORMAT.md](ADR-FORMAT.md)。

## During the session

### Challenge against the glossary

当用户使用的术语与知识库或当前对话已确认语言冲突时，立即指出。例如："现有口径里 cancellation 是 X，但你现在说的是 Y，哪一个算数？"

### Sharpen fuzzy language

当用户使用模糊或过载术语时，提出一个精确的 canonical term。"你说的 account，是 Customer 还是 User？这不是同一个东西。"

### Discuss concrete scenarios

讨论领域关系时，用具体场景做压力测试。发明能探测 edge cases 的场景，迫使用户精确定义概念边界。

### Cross-reference with code

当用户描述某事如何工作时，检查代码是否同意。发现矛盾时指出："代码会取消整个 Order，但你刚才说允许部分取消。以哪个为准？"

### Capture terms when they are resolved

一个 term 被确认后立刻记下来，不要攒到最后。会话中的临时叫法先留在对话；只有以后还要复用且确需持久化时，才写入目标项目的术语文档。

术语表必须完全不包含 implementation details。不要把 glossary 当 spec、草稿或实现决策仓库。它只说明一个词是什么，以及不要用哪些别名。

### Offer ADRs sparingly

只有以下三项都成立时，才提出创建 ADR：

1. **Hard to reverse** - 之后改变主意的成本有意义
2. **Surprising without context** - 未来读者会疑惑为什么这样做
3. **The result of a real trade-off** - 确实存在替代方案，并且基于具体理由选了其中一个

缺少任一项就跳过。ADR 记录决定和原因，不填空章节，也不写进提测或上线清单。
