---
name: prometheus-query
description: '通过 Prometheus 或 VictoriaMetrics 查询和分析 work 生产监控指标。用于查 CPU、内存、JVM、容器、Kubernetes、RocketMQ、Kafka、数据库和业务指标，处理监控告警、延迟或堆积、容量趋势、峰值时刻、PromQL、Grafana 指标取数，以及需要用时序数据验证线上现象的场景。'
---

# Prometheus / VictoriaMetrics 查询

使用受限只读脚本获取可复核的时序证据。把即时查询视为当前快照；判断历史告警、尖峰、抖动或堆积时，必须查询覆盖事件时间的 range 数据。

## 安全边界

- 只调用 `scripts/prometheus_query.py` 暴露的 Prometheus 只读 GET API；禁止调用写入、删除、管理或快照接口。
- 从本机 work 运行时配置读取 `prometheus_base_url`，不要在 Skill、脚本、参考资料、测试或最终答复中写入生产域名。
- 当前流程不需要用户名、密码、Token、Cookie 或 Session。以后增加认证时，把秘密放在本机凭证目录，不得提交到仓库；HTTP 地址不得承载任何认证信息。
- 把生产监控查询视为可能消耗资源的读取。优先使用精确指标名、精确 label 和最小有效时间窗，避免宽泛正则、高基数全量 series 和不必要的小 step。
- 脚本将 range 限制为最多 3 天、每条 series 最多 1440 个点，将指标和 series 探索限制为最多 24 小时并设置服务端扫描上限，同时限制响应体和展示条数。任何 `scan_truncated` 或 `truncated` 都必须作为证据限制报告。
- 脚本默认拒绝超过 3 天的 PromQL 内部回看窗口和超过 1440 个内部评估点的显式子查询。只有先向用户展示 PromQL、时间范围、step 和服务端风险并获得明确确认，才能使用 `--allow-expensive`；该开关不会取消 3 天外层 range、响应体或超时限制。
- Prometheus 的 `warnings`、`infos` 和 VictoriaMetrics 的查询统计会进入输出；VictoriaMetrics 返回 `isPartial=true` 时脚本直接拒绝结果，不能把部分响应当成完整证据。
- 默认只输出受限 JSON 摘要，不在当前项目目录落盘。确需保存原始响应时，使用系统 `TMP_DIR` 并避免把业务标签或实例清单提交到 Git。

## 前置配置

仅在运行时配置缺失或没有 `prometheus_base_url` 时，交互运行：

```bash
python3 "$AGENT_TOOLKIT_ROOT/common/bootstrap_work.py"
```

`prometheus_base_url` 对工具箱整体是可选项，但使用本 Skill 前必须配置。不要猜测、扫描或从其他文件复制生产地址。初始化会把地址写入本机 `~/.local/agent-toolkit/work/runtime.json`，该文件不进入 Git；已有其他配置时保留提示中的默认值。

使用项目 Python 运行时执行；Windows 通过项目依赖中的 `tzdata` 解析 IANA 时区：

```bash
"$AGENT_TOOLKIT_ROOT/.venv/bin/python" \
  "$AGENT_TOOLKIT_ROOT/skills/prometheus-query/scripts/prometheus_query.py" --help
```

Windows PowerShell 不假定 `PYTHON_BIN` 已导出为环境变量；从本机运行时配置读取真实解释器和工具包路径，再用调用运算符执行：

```powershell
$RuntimeFile = Join-Path $HOME ".local\agent-toolkit\work\runtime.json"
$Runtime = Get-Content -LiteralPath $RuntimeFile -Raw | ConvertFrom-Json
$PythonBin = [string]$Runtime.python_bin
$AgentToolkitRoot = [string]$Runtime.agent_toolkit_root
& "$PythonBin" "$AgentToolkitRoot\skills\prometheus-query\scripts\prometheus_query.py" --help
```

## 查询工作流

### 1. 明确问题和时间口径

先确认：

- 要解释当前状态，还是某次历史告警；
- 目标服务、环境、指标语义和可能的 label；
- 事件时间、时区、查询起止时间和采样 step；
- 需要按 pod、instance、broker、topic、group 观察，还是聚合后观察。

时间不明确时先请求最小必要信息。不得用“最近 12 小时”替代已知的事件窗口，也不得输出没有时区的时间。

### 2. 发现指标名

不知道名称时，按关键词过滤指标目录，避免直接打印全部结果：

```bash
"$AGENT_TOOLKIT_ROOT/.venv/bin/python" \
  "$AGENT_TOOLKIT_ROOT/skills/prometheus-query/scripts/prometheus_query.py" \
  metrics --contains rocket --contains lag --minutes 60 \
  --scan-limit 1000 --limit 100
```

多个 `--contains` 按 OR 匹配，且过滤发生在受限响应内。`scan_truncated=true` 表示可能仍有未扫描的指标名，不能把当前匹配数当成全局总数。

### 3. 检查 series 和 label

知道指标名后，先读取最近一小时的 series 结构。使用精确指标名，不猜 label：

```bash
"$AGENT_TOOLKIT_ROOT/.venv/bin/python" \
  "$AGENT_TOOLKIT_ROOT/skills/prometheus-query/scripts/prometheus_query.py" \
  series --metric rocketmq_group_diff --minutes 60 \
  --label 'group=example-group' --scan-limit 1000 --limit 50
```

`--label name=value` 是传给服务端的精确 matcher，可重复；不要在不了解 label 前猜值。`--contains` 只用于在受限响应内按 label 值做客户端 OR 过滤，不能替代精确 matcher，也不能降低服务端扫描范围。

### 4. 查询即时状态

即时值只能说明指定评估时刻的状态：

```bash
"$AGENT_TOOLKIT_ROOT/.venv/bin/python" \
  "$AGENT_TOOLKIT_ROOT/skills/prometheus-query/scripts/prometheus_query.py" \
  instant --query 'max(up{job="example"})' --timezone Asia/Singapore
```

查询历史某一时刻时显式传入带时区的 RFC 3339 时间或 Unix 时间戳：

```bash
"$AGENT_TOOLKIT_ROOT/.venv/bin/python" \
  "$AGENT_TOOLKIT_ROOT/skills/prometheus-query/scripts/prometheus_query.py" \
  instant --query 'max(up{job="example"})' \
  --time '2026-08-17T10:00:00+08:00' --timezone Asia/Singapore
```

### 5. 查询历史范围并形成结论

历史告警、尖峰和趋势必须使用 range。起止时间必须带时区，或使用 Unix 时间戳：

```bash
"$AGENT_TOOLKIT_ROOT/.venv/bin/python" \
  "$AGENT_TOOLKIT_ROOT/skills/prometheus-query/scripts/prometheus_query.py" \
  range --query 'max(rocketmq_group_diff{group="example-group"})' \
  --start '2026-08-17T08:00:00+08:00' \
  --end '2026-08-17T12:00:00+08:00' \
  --step 120 --timezone Asia/Singapore
```

脚本为每条 series 输出样本数、最小值、最大值、平均值、最后值、峰值时刻、前五个峰值和受限趋势采样。不要把多个不同 label 的峰值误当成同一条 series。

## 交叉验证

- 需要具体异常、请求或调用链时，再使用 `aliyun-sls-log-query` 查询峰值时刻附近的应用日志。
- 需要解释消费者、定时任务或聚合逻辑时，定位本地代码和部署配置；指标相关性不等于代码层根因。
- 需要读取生产业务状态时，再按 `archery-prod-query` 的只读和大表确认边界查询数据库。
- RocketMQ 延迟或堆积问题必须继续阅读 [references/rocketmq-triage.md](references/rocketmq-triage.md)。不要把 queue 数与消费线程的关系写成无条件定律。
- 需要 PromQL 聚合、counter、histogram 或 offset 示例时，阅读 [references/promql-recipes.md](references/promql-recipes.md)。

## 输出要求

根据实际执行的操作报告适用字段；不要为了填满模板而扩大生产查询。涉及 instant 或 range 的结论至少包含：

1. 实际执行的 PromQL；
2. 评估时间；range 还要包含起止时间、时区和 step；
3. 聚合和 label 口径；
4. 同时查询当前值和历史范围时，明确区分两者；
5. 涉及历史变化时，报告峰值、峰值时刻和趋势证据；
6. `warnings`、没有覆盖的时间、`scan_truncated`、`truncated` 或其他证据限制；
7. 根因判断，以及它是已证实事实、指标相关性还是待验证假设。

仅执行 metrics 或 series 发现时，报告时间窗口、关键词或精确 label、扫描上限、匹配数和截断状态即可，不得把发现结果写成业务根因。

查询为空时，只能说明指定 PromQL 和时间范围没有返回 series。先复核指标名、label、数据保留期和时间边界，不能直接断言历史上没有问题。
