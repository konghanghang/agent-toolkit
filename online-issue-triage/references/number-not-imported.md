# 号码为什么没导入任务

## 触发

- 运营反馈号码没导入
- 用户给号码 + 任务 ID，问为什么查不到

## 核心判断

先区分两件事：

1. 号码根本没进任务
2. 号码进了任务，但被拦截或后续没生效

## 第 1 步：明确 ID

号码可能是：

- 明文手机号
- `numberId`
- MD5 / SHA

任务 ID 可能是：

- `taskId`
- `flowDataId`
- `requestId`
- `flowTaskNodeDataId`

如果用户没说明，要先问清楚，或者先从数据库里交叉确认。

## 第 2 步：Archery 三连查

先按业务归属选实例：

- 国内传统业务：`archery list-databases --scope domestic`
- 国内新业务：`archery list-databases --module callback`
- 海外业务：`archery list-databases --scope overseas`

确认实例和库后，优先查：

```sql
SELECT * FROM ai_task WHERE id = <taskId>;

SELECT * FROM ai_task_detail
WHERE task_id = <taskId>
  AND (number = '<明文>' OR number = '<MD5>' OR number_id = <numberId>);

SELECT * FROM flow_data
WHERE task_id = <taskId> AND (...号码相关条件...);
```

重点拿到：

- 号码在不在任务里
- `status` / `error_status`
- 关联 `flowDataId` / `requestId` / `smsId`
- 创建时间 / 最后更新时间

## 第 3 步：SLS 反查

有了时间窗口和业务 ID 后，再用 `sls` 查：

```bash
sls query --scope domestic --service <service> --query '"<number>" and "<taskId>" and ERROR | with_pack_meta'
sls query --scope domestic --service <service> --query '"<taskId>" and "import" | with_pack_meta'
```

海外号码切海外 project。

## 常见结论

- `ai_task_detail` 查不到：根本没导入成功
- `ai_task_detail.status` 是拦截码：导入了但被拦截
- 状态正常但运营查不到：号码字段可能已 MD5
- 导入前被黑名单 / 去重 / 容量 / 权限拦截
