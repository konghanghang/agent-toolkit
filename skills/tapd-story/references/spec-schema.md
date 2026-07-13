# JSON 规格结构

## 单需求创建

```json
{
  "workspace_id": "10000000",
  "story": {
    "name": "【接口优化】统一回调状态字段",
    "description_html": "<h3>背景</h3><p>现有回调状态字段口径不一致。</p><h3>验收标准</h3><ol><li>状态字段使用统一枚举。</li></ol>"
  }
}
```

## 父子需求创建

```json
{
  "workspace_id": "10000000",
  "parent": {
    "name": "【系统改造】统一通知链路",
    "description_html": "<h3>背景</h3><p>统一多个通知渠道的处理边界。</p>"
  },
  "children": [
    {
      "name": "【系统改造】阶段一：入口收口",
      "description_html": "<h3>阶段目标</h3><p>统一请求入口和参数校验。</p>"
    }
  ]
}
```

## 批量更新

```json
{
  "workspace_id": "10000000",
  "stories": [
    {
      "id": "1110000000001000001",
      "description_html": "<h3>背景</h3><p>补充后的需求背景。</p>"
    }
  ]
}
```

## 字段规则

- `workspace_id` 可省略，随后从 `--workspace-id` 或 `TAPD_WORKSPACE_ID` 获取。
- `description_html` 必须使用允许的结构化 HTML。
- `owner` 可选且必须是字符串；创建时空字符串不会发送，更新时只有显式出现才会处理。
- `creator` 在任何规格中都禁止出现。
- 创建和更新规格不能混用。

完整示例见：

- `references/example-create-single.json`
- `references/example-create-parent-children.json`
- `references/example-update.json`
