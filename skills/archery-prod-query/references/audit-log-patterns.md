# 操作审计日志查询模式

本文件使用虚构的 `example_audit.demo_audit_log` 演示查询方法，不保存组织内的实例、库表、账号或鉴权映射。实际结构必须通过当前环境元数据和审计写入链路确认。

所有生产行数据查询继承主 Skill 的范围确认要求：执行前展示精确实例、库表、完整 SQL、时间与业务过滤、返回范围和扫描风险，并取得针对该范围的明确确认。

## 先确认环境与字段

```bash
archery list-instances-safe
archery list-databases-safe --instance "<已确认实例>"
archery list-tables-safe --instance "<已确认实例>" --database "<已确认数据库>"
```

随后用 `SHOW COLUMNS`、`DESC`、`SHOW CREATE TABLE` 确认实际审计表字段、时间类型、分区及排序键。元数据权限不足时停止，不改查业务明细、其他环境、历史实例或镜像表。

特别核对：

- 操作路径字段是否为完整请求路径，是否包含网关前缀。
- 操作方法字段表示 HTTP method、代码方法名还是业务动作。
- 用户标识、登录账号、应用密钥和租户字段的语义；它们可能随调用入口变化。
- 用户账号到审计用户 ID 的映射必须在同一环境、正确认证入口发现，不能复用其他系统的认证表。
- 请求参数、IP、账号和 JSON 内容可能包含敏感信息，默认不取；确需读取时限定范围并脱敏。

## 有限样本与统计

下面字段和表名都属于合成模型。替换成实际已确认结构后，再展示完整 SQL 并获得授权：

```sql
SELECT occurred_at, operation_path, operation_name, success
FROM example_audit.demo_audit_log
WHERE occurred_at >= '<开始时间>'
  AND occurred_at < '<结束时间>'
ORDER BY occurred_at DESC
LIMIT 20;
```

统计接口调用人数和次数时，可以起草：

```sql
SELECT operation_path, operation_name,
       count(DISTINCT user_id) AS caller_count,
       count(*) AS call_count
FROM example_audit.demo_audit_log
WHERE occurred_at >= '<开始时间>'
  AND occurred_at < '<结束时间>'
  AND operation_path = '<已确认路径>'
GROUP BY operation_path, operation_name
LIMIT 50;
```

ClickHouse 等数据库的精确去重函数、时间表达式与分区裁剪方式按实际引擎确认。`user_id` 或接口过滤不一定能减少分区扫描，不能因此省略时间范围。不要混合不同部署区结果，除非用户明确要求且查询范围已覆盖。

## 零结果与证据边界

操作审计日志不等于 HTTP 访问日志。没有对应审计记录，只能说明指定时间和条件下没有审计行，不能直接证明接口没有收到请求。

先核对环境、时区、用户映射、路径写法与过滤条件；再沿当前部署版本确认审计切面、开关、请求类型和租户上下文。不要无限扩大模糊条件，也不自动切换历史表、镜像或其他实例。
