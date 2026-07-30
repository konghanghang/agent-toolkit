# SLS 高级查询：聚合统计与避坑

`sls` 命令（`aliyun_sls_query.py`）默认 `pSql=false`，只能取**明细日志**。遇到精确总数、分组统计、条件筛选、超 100 条的列表时，必须用 `pSql=true` 自己发请求。本文记录 pSql 用法 + 这次实战踩过的坑。

---

## 什么时候必须用 pSql

| 需求 | sls 命令够吗 | 说明 |
|------|------------|------|
| 按 trace/关键词取明细 | ✅ 够 | `sls query --trace-id/--keyword` |
| 精确总数（count） | ❌ 不够 | `meta.count` 是返回行数，不是命中数 |
| 分组统计（GROUP BY） | ❌ 不够 | 命令不支持 |
| 条件筛选（HAVING） | ❌ 不够 | 命令不支持 |
| 列表 >100 条 | ❌ 不够 | `Size` 硬上限 100 |

---

## 用 sls agg 命令（首选）

封装好的子命令，复用 session 自动登录 + project/logstore 路由，不用手写 python：

```bash
# 精确去重总数（无 GROUP BY）
sls agg --service call-task-number \
        --sql 'SELECT count(distinct case when content like "%普通消息消费失败%" and content like "%606332%" then regexp_extract(content,"messageId[: ]*([0-9A-F]{20,})",1) end) as c' \
        --from 1785254400 --to 1785297600

# 分组统计（--index 限定日志子集再 GROUP BY）
sls agg --service call-task-number --index 'call-task-number and "普通消息消费失败"' \
        --sql 'SELECT regexp_extract(content,"messageId[: ]*([0-9A-F]{20,})",1) as mid, count(*) as c GROUP BY mid HAVING max(case when content like "%606332%" then 1 else 0 end)=1' \
        --from 1785286800 --to 1785304800 --size 100 --out /tmp/agg.json
```

参数：`--sql`（管道后 SQL，必选）、`--index`（管道前查询，默认 service 名或 `*`）、`--scope/--service` 或 `--project/--logstore`、`--from/--to` 或 `--minutes`、`--size`（1-100）、`--out`。需要取超过 100 个分组时，按时间段拆分查询后合并去重。

> 判断 pSql 是否生效看 `data` 是不是聚合结果行，**别看 `meta.hasSQL`**（pSql 模式下它可能仍显示 false，但 data 是准的）。

## pSql 请求模板（复用 skill 的 session, 复杂场景/调试用）

```python
import sys, json, requests
sys.path.insert(0, "<AGENT_TOOLKIT_ROOT>/skills/aliyun-sls-log-query/scripts")
import aliyun_sls_query as Q
creds = Q.load_creds(); sess = Q.load_session()
URL = "https://sls.console.aliyun.com/console/logs/getLogs.json"

def agg(sql_select, frm, to, size=100, index_q="call-task-number"):
    """index_q 是 SLS index 查询（管子前），sql_select 是 | 后的 SQL"""
    q = f'{index_q} | {sql_select}'
    b3, tp, ub = Q.gen_trace_ids()
    body = {
        "ProjectName": creds.get("project", ""), "LogStoreName": creds.get("logstore", ""),
        "from": frm, "to": to, "query": q, "Page": 1, "Size": size, "Reverse": "false",
        "pSql": "true", "fullComplete": "false", "schemaFree": "false", "needHighlight": "false",
    }
    headers = {
        "accept": "application/json", "content-type": "application/x-www-form-urlencoded",
        "origin": "https://sls.console.aliyun.com", "x-csrf-token": sess.get("csrf", ""),
        "referer": f'https://sls.console.aliyun.com/lognext/project/{creds.get("project","")}/logsearch/{creds.get("logstore","")}',
        "b3": b3, "traceparent": tp, "uber-trace-id": ub,
        "user-agent": sess.get("user_agent", "Mozilla/5.0"),
    }
    r = requests.post(URL, data=body, headers=headers, cookies=sess.get("cookies", {}), timeout=90)
    return r.json()

# 例：统计某关键词次数
print(agg('SELECT count(*) as c', 1785254400, 1785297600, index_q='call-task-number and "ERROR"'))
```

> 时间戳是秒级 Unix。北京时间 CST=UTC+8，换算：
> `python3 -c "import datetime;tz=datetime.timezone(datetime.timedelta(hours=8));print(int(datetime.datetime(2026,7,29,9,0,tzinfo=tz).timestamp()))"`
> 验证：告警飞书链接里的 `startTime`/`endTime` 是 CST 当天 0 点起的秒。

---

## ✅ 可靠模式（照这样写）

### 1. 精确去重总数 — `count(distinct case when ...)`，**无 GROUP BY**
```sql
SELECT count(distinct case when content like '%606332%' and content like '%普通消息消费失败%'
            then regexp_extract(content,'messageId[: ]*([0-9A-F]{20,})',1) end) as c
```
- 不受 100 行限制，给精确 distinct 总数
- **必须无 GROUP BY**（整表聚合，一行结果）
- 多组互斥条件相加 = 总数，可用来交叉验证（如 成功+跳过=总数 → 遗漏=0）

### 2. 中文 / 数字 token 一律用 `like`
```sql
content like '%导入白名单%'   -- 中文短语
content like '%606332%'       -- 纯数字也用 like
```
不要用 index query 的 `"中文"` 或 `"606332"`（见坑表）。

### 3. 分组：先 index 限定子集，再 GROUP BY + HAVING
```sql
-- q = 'call-task-number and "普通消息消费失败" | SELECT ... GROUP BY ... HAVING ...'
SELECT regexp_extract(content,'messageId[: ]*([0-9A-F]+)',1) as mid, count(*) as c
GROUP BY mid
HAVING max(case when content like '%606332%' then 1 else 0 end) = 1
```
关键：index 先把日志子集缩小（如限定某种日志类型），GROUP BY 才稳。

### 4. 绕 100 行限制 — 分时段合并去重
```python
all_ids = {}
for fr, to in [(t0, t0+600), (t0+600, t0+1200), ...]:   # 切 10 分钟段
    for r in agg(sql, fr, to).get('data', []):
        if r['mid'] not in all_ids:
            all_ids[r['mid']] = r
```

### 5. 跨字段关联 — 合并互补日志
某字段只出现在特定日志（如 `msgId` 在 PerfLog + 失败日志，`uuid` 在业务日志）。
同一事件的多条日志（554 ERROR + 575 sendErrorReport）**合并提取**，单源总有采集缺口。

---

## ❌ 坑表（别这样写，都有实战例子）

| 写法 | 现象 | 原因 |
|------|------|------|
| `count(*)` + 中文短语（index `and "中文"`） | 返回 0 | 中文未建索引 |
| index `"结果:true"` | 返回全部（过滤失效） | 短语索引不一致（`"结果:false"` 却正常） |
| index `"606332"`（纯数字） | 返回 0 | 数字 token 无索引 |
| `count(distinct case when...)` 在 **GROUP BY 下** | 返回 0 | 该构造仅无 GROUP BY 时 work |
| 全表扫描 + GROUP BY + 复杂 HAVING | 返回空 | 数据量大时不稳定 |
| `\| WHERE content like ...` | 返回 0 | pSql 不支持该 WHERE 位置 |
| 子查询 `FROM (SELECT...)` | 返回空 | 不支持 |
| `Size` 设为 100 以上 | 仍只返回 100 行 | 返回行数硬上限 100；`sls agg` 会直接拒绝 |

---

## 避坑心法：查不到 ≠ 没有

`count=0` / GROUP BY 空 / index 不匹配，都可能是**查询方式问题**，不是真的没有。
**换口径交叉验证**：like vs index、count vs GROUP BY、不同日志源——两个独立口径落同一区间，才敢下结论。

### 日志保留边界

pSQL 只能聚合仍在 SLS 保留期内的日志，不能恢复已经过期的数据。当前国内 SLS 仅保留最近 3 天。

- 查询前先按当前时间计算保留边界。
- 查询窗口跨越保留边界时，统计结果只代表仍可查询的区间，必须明确缺失区间。
- 对已经过期的区间，`count=0` 不可解释为没有日志，也不能用于排除某条调用链或历史行为。

> 实战：判断"重试消息是否都处理完"，用 `count(distinct case when)` 无 GROUP BY 算三组互斥数（成功 / 跳过 / 遗漏），相加 = 总数才坐实遗漏 0；单独跑全扫 HAVING 返回空，不可采信。

---

## msgId / traceId 关联（SLS 不稳定，优先 DB / MQ）

- `msgId` 只在 **PerfLog**（`ROCKET_MQ_CONSUMER`）和**失败日志**（554/575）
- 成功 / 跳过的业务日志只含 `uuid`，不含 `msgId`
- `uuid → traceId → msgId` 要 GROUP BY traceId 关联 PerfLog，但**全扫 GROUP BY traceId 不稳定**
- **要完整 msgId 列表（尤其成功/跳过的），优先 DB 查表 或 RocketMQ 控制台**，SLS 关联不靠谱

---

## 判断消费最终状态（RocketMQ 场景）

- 找"处理结束 结果:true/false"日志 + 幂等键（成功必打 true + 幂等）→ 可靠判据
- RocketMQ 默认重试 16 次（`maxReconsumeTimes=-1`），跨约 4 小时（间隔 10s→2h 递增）
- **判断"最终失败"必须在重试窗口（约首次失败 +4h）结束后**；中途的"从未成功"只是重试进行中，不是最终失败
