# PromQL 查询参考

仅在需要构造或解释 PromQL 时读取本文件。指标和 label 以运行时 `metrics`、`series` 返回结果为准，不要把示例名当成当前实例事实。

## API 语义

查询脚本只使用以下只读 API：

| API | 用途 |
|---|---|
| `/api/v1/label/__name__/values` | 发现指标名 |
| `/api/v1/series` | 查看精确指标的 label 结构 |
| `/api/v1/query` | 查询某一评估时刻 |
| `/api/v1/query_range` | 查询一个时间范围 |

即时查询可以通过 `time` 参数回看历史时刻，但它仍然只是单个评估点。判断持续时间、变化过程和尖峰时刻时使用 range。

## 常用表达式

```promql
# 保留每条原始 series
metric_name{label="value"}

# 所有匹配 series 的当前最大值
max(metric_name{label="value"})

# 跨 pod、instance 或 broker 求和
sum(metric_name{label="value"})

# 保留 topic 维度求和
sum by (topic) (metric_name{label="value"})

# counter 的每秒速率
rate(metric_total{label="value"}[5m])

# counter 在窗口内的增量
increase(metric_total{label="value"}[1h])

# gauge 在每个评估时刻回看 12 小时的最大值
max_over_time(metric_name{label="value"}[12h])

# 与前一天同一评估时刻比较
metric_name{label="value"} offset 1d
```

`max_over_time(...)` 只返回窗口内的最大数值，不返回该最大值发生的时间。需要峰值时刻时，执行 `range` 并使用脚本输出的 `peak_at`。

## Histogram 分位数

先确认指标确实是单调累积的 `_bucket` counter，并保留 `le`：

```promql
histogram_quantile(
  0.99,
  sum by (le) (rate(request_duration_seconds_bucket[5m]))
)
```

需要按服务保留维度时，把对应 label 与 `le` 一起分组：

```promql
histogram_quantile(
  0.99,
  sum by (service, le) (rate(request_duration_seconds_bucket[5m]))
)
```

不要把 gauge 当 counter 使用 `rate()`，也不要对已经是速率的 exporter 指标再次 `rate()`。先确认 HELP/TYPE、exporter 文档或数据形状。

## 聚合口径

聚合前回答两个问题：

1. 哪些 label 只是副本维度，例如 pod、instance 或 exporter endpoint；
2. 哪些 label 是业务维度，例如 topic、group、queue 或 region。

直接 `sum(metric)` 会丢掉所有 label，可能把不同业务对象混在一起。直接 `max(metric)` 可能只显示某一个副本的峰值。最终答复必须说明聚合方式。

## 时间窗口和 step

- 已知告警时间时，先覆盖告警前后足够解释变化的窄窗口。
- 不知道具体时间但知道日期时，先用较粗 step 查大势，再缩小到尖峰附近。
- step 决定返回的评估点，不等于原始采集间隔；过大的 step 可能错过短尖峰。
- range 子查询或 `*_over_time` 的回看窗口会在每个 step 重复计算，不能只根据输出点数判断服务端代价。
- 始终在命令和答复中写明时区。

## 常见指标族

以下仅用于发现关键词，不能替代运行时探测：

| 前缀 | 常见来源 | 典型用途 |
|---|---|---|
| `rocketmq_*` | RocketMQ exporter | 生产、消费、堆积和延迟 |
| `kafka_*` | Kafka exporter/JMX | lag、分区、生产和消费 |
| `node_*` | node_exporter | 主机 CPU、内存、磁盘和网络 |
| `container_*` | cAdvisor | 容器 CPU 和内存 |
| `kube_*`、`kubelet_*` | Kubernetes | Pod、Deployment 和节点状态 |
| `jvm_*` | JVM exporter | heap、GC 和线程 |
| `cloudmonitor_acs_*` | 云监控 exporter | RDS、PolarDB 等托管资源 |
| `grpc_*` | 应用或框架 exporter | RPC 请求量、状态和耗时 |

业务自定义指标必须先通过关键词发现，再读取 series；不要把历史存在过的指标族写成当前一定可用。

## 空结果处理

空结果常见原因包括：

- 指标名拼错或指标已改名；
- label 名或值不匹配；
- 查询窗口超出保留期；
- exporter 或 scrape target 在该时段不存在；
- 聚合、正则或 offset 改变了匹配集合。

依次验证指标名、series、时间和最小表达式。空结果不能证明历史上没有故障。
