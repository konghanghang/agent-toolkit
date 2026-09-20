# 术语格式

以后还要复用的稳定术语按目标项目约定落盘，并遵守该项目的文档格式。下面只约束术语正文怎么写。

## Structure

```md
# {Context Name}

{One or two sentence description of what this context is and why it exists.}

## Language

**Order**:
{A one or two sentence description of the term}
_Avoid_: Purchase, transaction

**Invoice**:
A request for payment sent to a customer after delivery.
_Avoid_: Bill, payment request

**Customer**:
A person or organization that places orders.
_Avoid_: Client, buyer, account
```

## Rules

- **Be opinionated.** 当多个词指向同一概念时，选出最好的那个，并把其他词列在 `_Avoid_` 下。
- **Keep definitions tight.** 最多一两句话。定义它是什么，而不是它做什么。
- **Only include terms specific to this project's context.** 一般编程概念（timeouts、error types、utility patterns）不属于这里，即使项目大量使用它们。添加 term 前先问：这是该 context 独有的概念，还是通用编程概念？只有前者属于这里。
- **Group terms under subheadings** when natural clusters emerge. 如果所有 terms 都属于一个 cohesive area，平铺列表也可以。
- 这次需求里的临时叫法留在对话里，不要仅为走完一次需求流程而新建术语文档。
