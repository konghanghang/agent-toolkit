# RocketMQ 延迟与堆积排查

仅在问题涉及 RocketMQ 消费延迟、堆积、生产消费速率或 queue/线程关系时读取本文件。

## 目录

- [先确认指标和 labels](#先确认指标和-labels)
- [常用指标](#常用指标)
- [排查流程](#排查流程)
- [根因判定](#根因判定)
- [queue 数与消费线程](#queue-数与消费线程)
- [历史案例](#历史案例)

## 先确认指标和 labels

下面的指标名来自既有排查经验，可能随 exporter 版本或采集配置变化。先执行：

```bash
PYTHON_BIN "$AGENT_TOOLKIT_ROOT/skills/prometheus-query/scripts/prometheus_query.py" \
  metrics --contains rocketmq --limit 200
```

再对准备使用的每个指标执行 `series`，确认 `group`、`topic`、`broker`、`instance` 等 label。不要根据历史命名规则直接拼生产查询。

历史上观察到过类似命名：

```text
group = cluster_<service>%<topic>
topic = cluster_<topic>
```

这只是发现线索，不是当前契约。

## 常用指标

| 指标名 | 常见含义 | 常见单位 | 重点 label |
|---|---|---|---|
| `rocketmq_group_diff` | 消费组未消费差值 | 条 | group、topic、broker |
| `rocketmq_group_get_latency_by_storetime` | 消息存储到消费拉取的时间差 | ms | group、broker |
| `rocketmq_producer_tps` | broker 观察到的生产速率 | 条/s | topic、broker |
| `rocketmq_client_consume_ok_msg_tps` | 客户端成功消费速率 | 条/s | group、instance/pod |
| `rocketmq_consumer_tps` | broker 侧拉取速率 | 条/s | group、broker |
| `rocketmq_client_consume_rt` | 客户端消费耗时 | ms | group、instance/pod |
| `rocketmq_client_consume_fail_msg_tps` | 客户端消费失败速率 | 条/s | group、instance/pod |
| `rocketmq_send_back_nums` | 消息回退或重试数量 | 次 | group、broker |

同名指标在不同 exporter 中语义和聚合方式可能不同。先用 series 确认是否按 broker、pod 或其他副本重复暴露，再决定 `sum`、`max` 或 `sum by (...)`。

## 排查流程

### 1. 查即时值，只描述现状

同时查看目标消费组的堆积、延迟、失败和回退。即时值低只能说明评估时刻已经恢复，不能否定之前的告警。

### 2. 查覆盖事件的 range

对 `group_diff` 和 latency 使用同一时间范围、时区和 step，记录：

- 峰值；
- 峰值时刻；
- 上升持续时间；
- 恢复持续时间；
- 多个 broker 或 pod 是否同步变化。

### 3. 对齐生产和消费

先确认指标单位一致，再在同一时间轴上比较：

```promql
sum(rocketmq_producer_tps{topic="<topic>"})

sum(rocketmq_client_consume_ok_msg_tps{group="<group>"})

max(rocketmq_group_diff{group="<group>"})
```

不要把 broker 侧 pull TPS 与客户端成功消费 TPS 当成同一口径。生产和消费指标的采样时间、exporter 延迟和缺失点也可能不同。

### 4. 缩小到峰值附近并交叉验证

用更窄时间窗和更细 step 复查尖峰。然后：

- 消费失败或回退上升：查同一时刻的应用异常日志；
- 客户端 consume RT 上升：查 GC、下游 RPC/数据库耗时和线程池状态；
- 生产突发：定位生产服务、定时任务或批量操作；
- 单个 pod 异常：检查 Pod 重启、CPU throttling、内存和网络指标。

## 根因判定

| 观测组合 | 优先假设 | 下一步证据 |
|---|---|---|
| 生产 TPS 突发高于消费，随后堆积上升 | 生产端突发或消费容量不足 | 找生产来源，核对消费容量和 queue 分配 |
| 生产稳定，消费 TPS 明显下降 | 消费端、下游或运行时卡顿 | consume RT、GC、CPU、下游指标和 SLS |
| consume fail 上升 | 消费处理失败 | 异常栈、重试策略和失败消息样本 |
| send back 上升 | 回退或重试 | 消费异常逻辑和重试队列 |
| 指标持续正常且告警仍触发 | 告警表达式或口径问题 | 告警 PromQL、for 时长、阈值和 label 聚合 |

表中内容是排查优先级，不是单一指标即可证明的根因。

## queue 数与消费线程

不要写“消费并行度永远等于 queue 数”或“调大 `consumeThreadMax` 永远无效”。应区分：

- clustering 模式通常把 queue 分配给消费组内的 consumer instance，queue 数会限制可同时获得分片的实例数量；
- 顺序消费对单 queue 的串行约束更强；
- 并发消费时，一个实例内部如何把消息批次提交给线程池，取决于客户端版本、listener 类型、pull/batch 配置和实现；
- 实际瓶颈还可能是下游依赖、单消息耗时、批量大小、限流、CPU 或 broker。

判断 queue 是否为瓶颈时至少验证：

1. topic 当前 queue 数；
2. 消费实例数和实际分配；
3. 使用顺序还是并发 listener；
4. 消费线程池的 active、queue 和任务耗时；
5. 单 queue 或单实例吞吐；
6. 扩容实例、调整线程或增加 queue 前后的实际指标变化。

只有这些证据支持时，才能把增加 queue 作为解决方案。

## 历史案例

以下是 2026-08-04 一次 `common-hangup_public` 告警的历史快照，只用于展示推理方法，不代表当前配置或水位。

已观察到：

1. 即时 `group_diff` 只有 85 条，看起来已经恢复；
2. 覆盖事件的 range 显示堆积峰值约 672,634 条，延迟峰值约 356 秒；
3. 一个峰值时刻附近，生产约 6,774 条/s，消费约 1,228 条/s；
4. 生产下降后，消费继续消化存量，恢复时间与告警延迟量级一致。

该案例能够支持“生产突发超过当时消费能力，形成真实堆积”，也说明即时低水位不能否定历史告警。它不能单独证明任何环境当前仍有相同 queue 数，也不能证明所有 RocketMQ 消费者的线程上限都由 queue 数决定。
