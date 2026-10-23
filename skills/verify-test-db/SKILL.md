---
name: verify-test-db
description: '安全查询和受控修改一个或多个测试环境 MySQL 实例，自动发现数据库所在实例，并通过操作前快照、操作后重查、字段级差异、预期值断言和异步轮询判断功能是否正确。用于“查测试库”“检查数据有没有入库”“确认修改是否生效”“比较操作前后数据”“等待异步入库”“帮我造测试数据”“清理本次测试数据”等场景；只连接显式标记为 test 的 profile，查询结果默认脱敏，所有真实写入必须先生成并确认带回滚 SQL 的计划。'
---

# 测试库验证

把测试库操作组织成可重复的验证闭环：先确定目标记录和预期，再记录基线、执行或等待业务操作、重查并断言，最后给出 `PASS`、`FAIL` 或 `INCONCLUSIVE`。优先使用 `testdb` 入口，不要临时拼接数据库客户端命令或读取本机 SQL 客户端凭据。

## 前置条件

1. 使用 `scripts/install.py` 安装本技能，或运行 `python3 "$AGENT_TOOLKIT_ROOT/common/bootstrap_work.py"` 初始化公共运行时和 `testdb` 入口。两种路径都会在文件不存在时生成私有的 `TEST_DB_DIR/profiles.json` 模板，已有文件绝不覆盖。
2. 按 [references/configuration.md](references/configuration.md) 为每个物理测试实例配置一个 profile；只维护实例地址、账号和密码环境变量，数据库范围可以使用 `["*"]`。
3. 只使用 `environment: "test"` 的 MySQL profile。需要 VPN 或 SSH 隧道时，先由用户建立连接，再把实际的 host/port 配到 profile；本技能不自动创建隧道。

如果缺少 profile、网络、账号或权限，报告准确缺口并停止。不得读取 DataGrip、Navicat 或其他 SQL 客户端的密码来绕过配置。

某个产品的库表落在哪套实例，由调用方的业务技能提供。不要把 work 或其他产品的表清单写进本技能。

## 安全边界

- 把 `allowed_databases` 作为读取范围，把 `writable_databases` 作为写入范围；两者都支持单独的 `["*"]`。
- 把空数组解释为不允许任何库；禁止把 `*` 和具体库名混用。
- 即使配置 `*`，也始终拒绝 `mysql`、`information_schema`、`performance_schema` 和 `sys`。
- 省略 `--profile` 时，连接所有可能包含目标库的测试实例，通过数据库和目标表元数据选择唯一实例，并把结果缓存 24 小时。任何候选实例探测失败或仍有多个匹配时停止，不静默猜测。
- 显式 `--profile` 时不自动探测；需要忽略缓存时使用 `--refresh-route`。写入计划始终重新探测，不使用旧路由缓存。
- 只通过 `query-safe` 执行单条 `SELECT`；拒绝多语句、锁、导出文件和危险函数，并限制返回行数。
- 默认脱敏手机号、邮箱、密码、Token、Cookie、证件号等字段。快照用本机 HMAC 摘要保持前后可比较，不显示原值。
- 不把“测试环境”解释成“可以随意修改”。任何真实写入都必须经过写入计划、用户确认和事务影响行数保护。
- 禁止 DDL。写入只允许单表 `INSERT`、带 `WHERE` 的 `UPDATE` 或带 `WHERE` 的 `DELETE`，并且必须提供回滚 SQL。

## 查询和验证工作流

### 1. 缩小数据范围

先确定 profile、数据库、表、业务主键、企业/任务标识和必要时间范围。共享测试环境可能同时有其他人操作，不要用全表数量或宽泛时间范围代替目标记录。

```bash
testdb profiles-safe
testdb databases-safe --profile test-main
testdb resolve-safe --database ai --table demo_task
testdb tables-safe --database ai
testdb describe-safe --database ai --table demo_task
```

Windows PowerShell 中优先调用初始化生成的公共 `.cmd` 入口，不直接拼接底层 Python 脚本：

```powershell
$AgentToolkitRoot = (Resolve-Path (Join-Path $HOME ".agent-toolkit")).Path
& "$AgentToolkitRoot\bin\testdb.cmd" profiles-safe
& "$AgentToolkitRoot\bin\testdb.cmd" describe-safe --database ai --table demo_task
```

通常省略 `--profile`，让工具自动定位实例；多个实例同时存在同名库和同名表时，再显式指定 `--profile`。`databases-safe` 没有目标库可用于定位，因此仍要求 profile。

使用精确 `SELECT` 查询，默认最多返回 100 行，最大 500 行：

```bash
testdb query-safe \
  --database ai \
  --sql 'select id, status, update_time from demo_task where id = 12345' \
  --limit 10 \
  --format json
```

### 2. 在业务操作前记录基线

为对比选择稳定且唯一的一个或多个 `--key`。快照保存查询本身和脱敏结果；结果超过 limit 时拒绝保存，要求进一步缩小范围。

```bash
testdb snapshot-safe \
  --database ai \
  --sql 'select id, status, update_time from demo_task where id = 12345' \
  --key id \
  --label before-update
```

记录返回的 `snapshot_id`。如果业务操作已经发生且之前没有快照，不得声称已完成严格的前后对比；只能验证当前状态，并把结论标为当前状态证据或 `INCONCLUSIVE`。

### 3. 操作后比较或轮询断言

直接查看字段级变化：

```bash
testdb compare-safe --snapshot <snapshot_id>
```

等待异步入库并验证预期值：

```bash
testdb verify-safe \
  --snapshot <snapshot_id> \
  --expect 'status=2' \
  --expect-changed status \
  --expect-row-count 1 \
  --timeout 30 \
  --interval 2
```

没有基线时，也可以只验证当前状态：

```bash
testdb verify-safe \
  --database ai \
  --sql 'select id, status from demo_task where id = 12345' \
  --expect 'status=2' \
  --expect-row-count 1
```

一次轮询最长 60 秒。超过后重新评估日志、消息队列、服务错误或查询条件，不要无限等待。

## 受控造数和修改

先生成计划，不执行 SQL：

```bash
testdb write-plan-safe \
  --database ai \
  --sql 'insert into test_fixture(id, status) values (9001, 1)' \
  --rollback-sql 'delete from test_fixture where id = 9001' \
  --min-affected 1 \
  --max-affected 1 \
  --reason '验证任务入库流程'
```

生成写入计划时强制刷新实例路由。把输出中的最终 profile、host、database、完整 SQL、回滚 SQL、最大影响行数和过期时间展示给用户。暂停并等待用户针对该计划明确确认；“查一下”或普通功能验证请求不构成写入授权。

确认后才执行：

```bash
testdb write-apply --plan <plan_id> --confirm <confirmation_token>
```

需要清理或恢复时，再次展示计划中的回滚 SQL并获得确认，然后执行：

```bash
testdb write-rollback --plan <plan_id> --confirm <confirmation_token>
```

`confirmation_token` 绑定 profile、目标库、完整 SQL、回滚 SQL 和影响行数范围，计划内容改变后原确认自动失效。`write-apply` 和 `write-rollback` 不属于默认放行命令。同一计划使用独占执行锁；影响行数超出计划范围时脚本回滚事务并关闭该计划。状态不明确或遗留执行锁的计划不得重复执行，必须重新检查数据库后生成新计划。

## 结论格式

最终结果至少包含：

- 使用的 test profile、数据库、目标表和查询业务范围
- 是否存在可信的操作前快照
- 新增、删除、变化和未变化的行数
- 关键字段的预期与实际结果，不披露被脱敏的原值
- `PASS`：查询和断言都成功，证据覆盖用户要验证的功能结果
- `FAIL`：查询成功，但超时后仍不满足明确断言，或出现非预期字段变化
- `INCONCLUSIVE`：缺少基线、连接/权限不足、查询范围不可信，或数据库结果无法单独证明完整功能链路

区分本地脚本测试和真实测试库验证。没有成功连接并读取目标测试库时，只能报告本地验证通过，不能称为数据库 E2E 验证。
