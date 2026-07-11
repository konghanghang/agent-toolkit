# 任务流程为什么没有继续推进

## 先定位停止阶段

区分任务尚未生成流程实例、实例停在某个节点，以及节点执行失败或等待重试。主任务状态不能代替节点级状态。

确认任务、实例、节点运行和请求 ID 各自含义，结合租户和时间窗口定位实际表；关系发现方法见 [数据关系速查](table-cheatsheet.md)。

## 数据与日志证据

先通过已授权的数据库入口确认当前状态、最后更新时间、重试与锁信息。以下只是虚构查询结构：

```sql
SELECT id, task_id, status, current_node, updated_at
FROM example_db.demo_flow_run
WHERE task_id = <任务 ID>
LIMIT 20;

SELECT id, flow_id, status, request_id, updated_at
FROM example_db.demo_flow_node_run
WHERE flow_id = <流程实例 ID>
LIMIT 50;
```

替换为当前环境实际结构，展示完整范围并取得对应查询授权。再用相同时间窗口和请求标识查询已确认服务：

```bash
sls search-safe --scope domestic --service example-flow-service \
  --trace-id <关联 trace ID> --minutes 60 --size 20
```

测试环境使用 JumpServer；部署区不明时先确认。

## 沿执行链验证

在真实节点调度和执行入口中核对前置条件、幂等、锁、状态迁移、MQ 消费、回调及补偿。异常名或状态为 ERROR 只是线索，必须结合执行分支说明停在哪一步。

- 前置数据不一致：确认依赖节点是否完成，不能直接改状态绕过校验。
- 锁竞争：确认持有者、超时及释放路径，不自动解锁。
- 等待消息或回调：确认实际投递、消费与重试证据。
- 数据看似正常但不推进：继续检查当前部署版本、调度器和缓存，不把本地代码等同于运行态。

重试、补偿、解锁或状态修复另需对应授权。最终区分已确认根因、尚待验证假设和建议动作。
