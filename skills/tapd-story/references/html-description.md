# TAPD HTML 描述规范

## 允许标签

只使用：

```text
h3 p ul ol li strong code table thead tbody tr th td br a
```

表格单元格只允许 `colspan` 和 `rowspan` 属性；`a` 只允许 HTTP/HTTPS `href`。禁止 `style`、事件属性、脚本、iframe 和任意嵌入内容。

## 推荐结构

普通需求按内容取舍：

- 背景
- 目标
- 范围
- 关键任务
- 依赖
- 风险
- 验收标准

总需求优先使用：

- 背景
- 目标与非目标
- 当前事实
- 方案设计
- 影响范围
- 验证方式
- 落地后文档处理

子任务至少包含：

- 阶段目标
- 覆盖范围
- 关键任务
- 前置依赖
- 风险点
- 验收标准

保持段落短、列表清楚。不要把 Markdown 原文直接塞入 TAPD，也不要用一个长段落承载全部内容。
