# 联系对象为什么没有导入任务

## 先区分现象

对象可能没有写入任务，也可能已经写入但被后续规则拦截。不要只根据页面查不到就判断导入失败。

先确认任务标识、对象标识、环境、导入批次和时间窗口。对象标识可能是原始号码、业务 ID 或哈希；不同 ID 不能互相代用。缺少会改变查询范围的信息时，优先问一个关键问题。

## 从数据确认导入阶段

通过 `$archery-prod-query` 或 `$verify-test-db` 按环境选择已授权入口。先用元数据和代码定位实际任务、明细、批次及去重记录，再起草限定租户、任务、对象和时间的查询。

下面仅是虚构模型，实际表名和字段必须替换：

```sql
SELECT id, status
FROM example_db.demo_task
WHERE id = <任务 ID>;

SELECT id, task_id, status, failure_reason
FROM example_db.demo_task_detail
WHERE task_id = <任务 ID>
  AND contact_id = <对象 ID>
LIMIT 20;
```

生产行数据查询必须先展示完整 SQL 和范围，并取得对应授权；这里的模板不构成执行许可。

## 从日志和代码验证原因

有时间窗口和关联 ID 后，按已确认的服务路由查日志：

```bash
sls search-safe --scope domestic --service example-import-service \
  --trace-id <关联 trace ID> --minutes 60 --size 20
```

测试环境按 JumpServer 路径查询，不因没有日志就切换生产。随后在真实导入链路检查去重、拦截、容量、权限、事务、异步落库和异常处理分支。

## 结论边界

- 明细不存在：继续确认查询范围、异步时序、事务和导入结果，不能单凭一次查询认定根因。
- 明细存在但状态异常：用枚举和实际分支解释状态。
- 页面与数据不一致：确认筛选、加密形式、权限与缓存。
- 需要补导入、重试或修数据时，另走相应写入流程，不在只读排查中自动执行。
