# 指标查询模板

## 环境变量

直接运行脚本前，先在本机准备下面这些环境变量：

```text
ARCHERY_BASE_URL
ARCHERY_CSRF_TOKEN
ARCHERY_COOKIE
```

在 Windows 下，`scripts/archery_query.py` 会按下面顺序读取：

1. 当前进程环境变量
2. `HKCU\Environment`
3. 机器级环境变量（如果存在）

PowerShell 临时设置示例：

```powershell
$env:ARCHERY_BASE_URL = "https://archery-k8s.94ai.pro"
$env:ARCHERY_CSRF_TOKEN = "your-csrf-token"
$env:ARCHERY_COOKIE = "_ga=...; csrftoken=...; sessionid=...; ..."
```

PowerShell 持久化到用户环境变量示例：

```powershell
[Environment]::SetEnvironmentVariable("ARCHERY_BASE_URL", "https://archery-k8s.94ai.pro", "User")
[Environment]::SetEnvironmentVariable("ARCHERY_CSRF_TOKEN", "your-csrf-token", "User")
[Environment]::SetEnvironmentVariable("ARCHERY_COOKIE", "_ga=...; csrftoken=...; sessionid=...; ...", "User")
```

## 实例发现

列出可读实例：

```bash
"$PYTHON_BIN" "$AGENT_TOOLKIT_ROOT/skills/archery-prod-query/scripts/archery_query.py" list-instances
```

列出数据库：

```bash
"$PYTHON_BIN" "$AGENT_TOOLKIT_ROOT/skills/archery-prod-query/scripts/archery_query.py" list-databases --instance "main-db"
```

列出表：

```bash
"$PYTHON_BIN" "$AGENT_TOOLKIT_ROOT/skills/archery-prod-query/scripts/archery_query.py" list-tables --instance "main-db" --database "ai_admin"
```

## 新智能任务

关键区分：

- 新智能任务数量 = 去重后的智能任务 ID 数量
- 在当前 `ai.ai_task` 口径里，智能任务数量用 `count(distinct flow_id)`
- `ai_task.task_id` 是节点外呼任务 ID，只能用来映射 `stat_day_base.task_id`，不能当智能任务数量
- 不要用 `count(*)` 或 `count(distinct task_id)` 计算新智能任务数量

### 上半年智能任务数量

智能任务数量使用 `count(distinct flow_id)`，不要把 `ai_task` 里的 `count(*)` 当周报任务量。

```sql
select
  count(distinct flow_id) as smart_task_cnt,
  count(distinct company_id) as company_cnt
from ai_task
where create_time >= '2026-01-01'
  and create_time < '2026-07-01'
  and flow_id is not null;
```

### 月趋势

```sql
select
  date_format(create_time, '%Y-%m') as month,
  count(distinct flow_id) as smart_task_cnt
from ai_task
where create_time >= '2026-01-01'
  and create_time < '2026-07-01'
  and flow_id is not null
group by date_format(create_time, '%Y-%m')
order by month;
```

### 上半年外呼量

先确定智能任务 ID 范围，再把关联的节点外呼任务映射到事实表：

```sql
select task_id
from ai_task
where create_time >= '2026-01-01'
  and create_time < '2026-07-01'
  and flow_id is not null;
```

然后按收集到的节点 `task_id` 列表聚合 `stat_day_base`。输出时要明确：

- 任务数量按智能任务 ID 统计
- 外呼量按关联的节点外呼任务统计

## 旧智能任务

### 国内任务数量

```sql
select
  count(*) as smart_task_cnt,
  count(distinct company_id) as company_cnt
from t_smart_task
where create_time >= '2026-01-01'
  and create_time < '2026-07-01';
```

### 国内分支映射数量

```sql
select
  count(*) as branch_cnt,
  count(distinct target_task_id) as target_task_cnt
from t_smart_task_branch
where target_task_id is not null
  and task_id in (
    select id
    from t_smart_task
    where create_time >= '2026-01-01'
      and create_time < '2026-07-01'
  );
```

### 国内事实表聚合

涉及表：

- `auto-db.ai_task.t_smart_task`
- `auto-db.ai_task.t_smart_task_branch`
- `log-db.auto_dialer_data.stat_day_base`

按映射出的外呼任务 ID 聚合：

```sql
select
  count(distinct task_id) as task_cnt,
  sum(import_number_count) as import_number_count,
  sum(call_number_count) as call_number_count
from stat_day_base
where stat_date >= '2026-01-01'
  and stat_date < '2026-07-01'
  and task_id in (...mapped target_task_id list...);
```

### 海外事实表聚合

逻辑与国内一致，但任务表、分支表和 `stat_day_base` 都要使用海外生产实例。

## `stat_day_base`

只有在业务口径已确认时才使用，不要默认它代表全平台总量。

### 聚合模板

```sql
select
  count(distinct company_id) as company_cnt,
  sum(import_number_count) as import_number_count,
  sum(call_number_count) as call_number_count,
  sum(call_count) as call_count,
  sum(answer_count) as answer_count,
  sum(ab_count) as ab_count,
  sum(sms_count) as sms_count
from stat_day_base
where stat_date >= '2026-01-01'
  and stat_date < '2026-07-01';
```

### 月趋势

```sql
select
  date_format(stat_date, '%Y-%m') as month,
  sum(import_number_count) as import_number_count,
  sum(call_number_count) as call_number_count,
  sum(answer_count) as answer_count,
  sum(ab_count) as ab_count,
  sum(sms_count) as sms_count
from stat_day_base
where stat_date >= '2026-01-01'
  and stat_date < '2026-07-01'
group by date_format(stat_date, '%Y-%m')
order by month;
```

## 抖店

已知拆分路径：

```text
polardb-tidb.tiktok_shop.t_shop_task.task_id -> stat_day_base.task_id
```

推荐流程：

1. 查询 `t_shop_task`，确定相关 `task_id`
2. 用这些 `task_id` 过滤 `stat_day_base`
3. 按月、`task_scene_id` 或 `type` 做聚合（如果需要）

如果 `tiktok_shop` 没权限，不要用无关字段硬估，直接申请：

```text
polardb-tidb.tiktok_shop.t_shop_task
polardb-tidb.tiktok_shop.t_scene_config
```

## 权限申请话术

直接使用类似下面的话术：

- `The metric path is confirmed, but read access to main-db.tiktok_shop.t_shop_task is missing, so TikTok Shop tasks cannot yet be mapped to stat_day_base.task_id.`
- `Only the total new smart-task dataset is currently available; TikTok Shop cannot yet be split out as a separate metric.`

## 分页模式

当映射结果可能超过一页时，先对源 ID 分页：

```sql
select distinct target_task_id
from t_smart_task_branch
where ...
order by target_task_id
limit 0, 200;
```

然后继续：

```sql
limit 200, 200
limit 400, 200
limit 600, 200
```

把所有返回 ID 收齐、去重，再对 `stat_day_base` 执行最终聚合。

## 口径表述

日期说明要具体：

- `As of 2026-06-21, task-creation data is current through 2026-06-21.`
- `As of 2026-06-21, outbound and settlement fact tables are current through 2026-06-20.`

归因表述建议写成：

- `These metrics are reported only as business results for the user's owned module, not as ownership of the entire platform total.`
