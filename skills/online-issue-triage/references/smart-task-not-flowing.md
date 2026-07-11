# 号码在智能任务为什么没流转

## 触发

- 号码进了任务，但流程没继续
- 智能外呼 / 流程节点卡住

## 核心判断

先区分：

1. 号码进了任务，但没生成流程实例
2. 流程实例生成了，但卡在某个节点
3. 节点继续执行失败，进入 ERROR / 等待执行状态

## 第 1 步：Archery 查流程状态

先按业务归属选实例，再查：

```sql
SELECT flow_data_id, status, error_status, current_node, create_time, alter_time
FROM flow_data
WHERE task_id = <taskId> AND (...号码相关条件...);

SELECT flow_task_node_data_id, flow_data_id, status, error_status, request_id, alter_time
FROM flow_task_node_data
WHERE flow_data_id IN (<flowDataId 列表>);

SELECT *
FROM flow_task_node_data
WHERE request_id = <requestId>;
```

重点看：

- `flow_data.status`
- `flow_task_node_data.status`
- `error_status`
- `requestId`
- 当前节点和最后更新时间

## 第 2 步：SLS 查节点异常

拿到 `requestId`、`flowDataId`、时间窗口后，用 `sls` 查：

```bash
sls query --scope domestic --service ai-flow-server --query '"<requestId>" | with_pack_meta'
sls query --scope overseas --service ai-flow-server --query '"<flowDataId>" and ERROR | with_pack_meta'
```

如果不知道国内还是海外，先问清部署区，不要乱查。

## 第 3 步：代码定位

典型项目：

- `call/ai-flow/ai-flow-server`

重点类：

- `ConditionDiverterFlowListener`
- `BaseFlowListener.preExecute`
- `DbConsistencyChecker.checkAndGetExecutingData`
- `LockTemplate.execute`

## 常见异常对照

- `DataInconsistencyException: 存在等待执行的节点`
  - 上游节点未完成 / 锁未释放
- `AppBusinessException`
  - 业务前置条件不满足
- `CannotAcquireLockException`
  - 锁竞争
- `flow_data.status = ERROR`
  - 节点执行异常退出
- 数据正常但没动
  - MQ / 缓存问题
