# 指标查询模式

本文只提供方法与虚构 SQL，不保存组织内部的指标路径、实例或表结构。所有示例中的 `example_*`、`demo_*` 都需要用实际代码、元数据和业务口径确认后替换。

## 生产大表安全门

事实表、历史表、月表和聚合表的行数据查询，必须先向用户展示精确实例、库表、完整 SQL、租户/任务/时间过滤与扫描风险，取得针对该范围的明确确认后再执行。未经确认只查元数据。

权限不足时报告缺口，不切换其他实例、镜像、历史库或无关字段估算结果。

## 配置与实例发现

真实地址和鉴权读取本机私有配置。直接调用底层脚本时，按主 Skill 的说明提供 `ARCHERY_BASE_URL`、`ARCHERY_CSRF_TOKEN`、`ARCHERY_COOKIE`；它们不进入仓库。

PowerShell 中的地址示例是保留域名，不可直接连接：

```powershell
$env:ARCHERY_BASE_URL = "https://archery.example.invalid"
$env:ARCHERY_CSRF_TOKEN = "<本机 csrf token>"
$env:ARCHERY_COOKIE = "<本机 cookie>"
```

通过元数据入口发现实际对象：

```bash
archery list-instances-safe
archery list-databases-safe --instance "<已确认实例>"
archery list-tables-safe --instance "<已确认实例>" --database "<已确认数据库>"
```

## 先定义统计对象

- 区分业务实体 ID、子任务 ID、执行次数和事实行数。
- 一个业务实体对应多个执行节点时，实体数量不能直接用节点数或 `count(*)` 代替。
- 通过代码、主外键和受限样本验证实体到事实表的关联；不要只凭字段名相似连接。
- 明确租户、模块、环境、自然日/月和数据截止时间，区分事件发生时间与数据入库时间。
- 业务子集缺少映射权限时，仅报告已取得的总体口径，不能把总体数归给子集。

## 虚构月趋势模板

模板只用于起草；填好真实对象和范围、展示完整 SQL 并取得授权后才执行：

```sql
SELECT DATE_FORMAT(created_at, '%Y-%m') AS month,
       count(DISTINCT business_id) AS entity_count
FROM example_metrics.demo_business_entity
WHERE tenant_id = <租户 ID>
  AND created_at >= '<开始时间>'
  AND created_at < '<结束时间>'
GROUP BY DATE_FORMAT(created_at, '%Y-%m')
ORDER BY month;
```

实际数据库不支持相同函数时，按引擎语法调整。需要聚合执行量时，先确认实体到执行 ID 的映射，再起草限定这些 ID 和时间的事实查询，不能默认把实体数量等同于执行次数。

## 分页与关联

映射结果超过一页时，用稳定唯一键排序分页，收齐并去重后再起草最终聚合。页数、返回行数和完整性分别记录；截断结果不能声称完整。

关联对象发现与事实聚合可能涉及不同表和权限。每次实际查询都须位于已确认范围，新增表、扩大时间、切换实例或增加敏感字段时重新确认相应变化。

## 输出

写明业务口径、时区、起止时间、数据截止时间、结果是否完整，以及缺少的映射或权限。部署成功、单条样本和部分月份结果不能分别代替完整业务指标验收。
