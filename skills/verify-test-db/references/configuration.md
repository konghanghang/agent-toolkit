# 测试库配置

## 配置位置

运行公共初始化：

```bash
python3 "$AGENT_TOOLKIT_ROOT/common/bootstrap_work.py"
```

安装器和公共初始化都会在 `profiles.json` 不存在时自动生成模板；已经存在的文件绝不覆盖。公共初始化会把 `test_db_dir` 写入 `~/.local/agent-toolkit/work/runtime.json`，默认目录为 `~/.local/agent-toolkit/work/test-db`。自动生成的内容为：

```json
{
  "profiles": {
    "test-main": {
      "environment": "test",
      "engine": "mysql",
      "host": "测试库地址",
      "port": 3306,
      "username": "测试库账号",
      "password_env": "TEST_MAIN_DB_PASSWORD",
      "allowed_databases": ["*"],
      "writable_databases": ["*"]
    }
  }
}
```

脚本会在读取或写入前收紧权限：macOS/Linux 使用 `0600` 文件与 `0700` 目录；Windows 使用关闭继承的受保护 ACL，只保留当前用户、`SYSTEM` 和内置管理员。如果文件是手工复制的，后续运行 `testdb` 时也会先完成权限保护。macOS/Linux 仍可手工确认：

```bash
chmod 600 ~/.local/agent-toolkit/work/test-db/profiles.json
```

先替换“测试库地址”和“测试库账号”，再设置 `TEST_MAIN_DB_PASSWORD`。profile 支持直接使用 `password`，也支持用 `password_env` 指向进程环境变量；两者只能配置一个。`profiles-safe` 只显示密码来源和模板是否配置完成，不显示密码。模板占位符未替换时，连接和写入计划会在本地直接拒绝。

## 多实例自动定位

为每个物理 MySQL 实例配置一个 profile，不需要手工列出该实例上的全部数据库：

```json
{
  "profiles": {
    "test-main": {
      "environment": "test",
      "engine": "mysql",
      "host": "实例一地址",
      "port": 3306,
      "username": "测试库账号",
      "password_env": "TEST_MAIN_DB_PASSWORD",
      "allowed_databases": ["*"],
      "writable_databases": ["*"]
    },
    "test-task": {
      "environment": "test",
      "engine": "mysql",
      "host": "实例二地址",
      "port": 3306,
      "username": "测试库账号",
      "password_env": "TEST_TASK_DB_PASSWORD",
      "allowed_databases": ["*"],
      "writable_databases": ["*"]
    }
  }
}
```

数据库命令省略 `--profile` 时会自动连接候选实例读取数据库列表。如果多个实例都有同名库，则继续用目标表缩小范围；最终只有一个实例匹配时才执行。任何候选实例不可达，或同名库和同名表仍无法区分时，命令停止并要求显式指定 profile。

自动发现结果保存在私有的 `database-routes.json` 中，24 小时后过期；profile 地址、账号或数据库范围改变时立即失效。使用 `--refresh-route` 可以强制重新发现。生成写入计划时始终重新发现，不使用缓存。

```bash
testdb resolve-safe --database demo_task --table demo_task
testdb query-safe --database demo_task --sql 'select id, status from demo_task where id = 1'
```

`databases-safe` 因为没有目标数据库，仍需指定 `--profile`。显式传入 profile 的其他命令也保持原行为，不进行自动定位。

## 数据库范围语义

读取和写入范围分别配置：

```json
{
  "allowed_databases": ["ai", "example_admin"],
  "writable_databases": ["ai"]
}
```

语义如下：

- `allowed_databases: []`：禁止读取任何业务库。
- `writable_databases: []`：禁止写入，profile 为只读。
- `["ai", "example_admin"]`：只允许列出的库。
- `["*"]`：允许该测试实例中的所有非系统业务库。
- `["*", "ai"]`：非法配置，拒绝加载。
- `writable_databases` 必须是 `allowed_databases` 的子集。
- `writable_databases: ["*"]` 要求 `allowed_databases` 同样为 `["*"]`。

无论范围如何配置，始终拒绝 `mysql`、`information_schema`、`performance_schema` 和 `sys`。

## 连接方式

第一版使用直接 MySQL 连接：

- VPN 后可直连时，配置真实测试库 host/port。
- 已建立本地 SSH 隧道时，配置 `127.0.0.1` 和本地转发端口。
- 不自动读取 DataGrip、Navicat 等客户端配置。
- 不自动创建 SSH 或 JumpServer 隧道。

需要 CA 时可以增加：

```json
{
  "ssl_ca": "/absolute/path/to/ca.pem"
}
```

## 本机状态

`TEST_DB_DIR` 下会生成：

```text
test-db/
├── profiles.json
├── database-routes.json
├── redaction.key
├── snapshots/
└── plans/
```

- `redaction.key` 用于生成稳定 HMAC，保证敏感字段可比较但不显示原值。
- `database-routes.json` 保存短期数据库/表到实例的自动发现结果，不包含密码。
- `snapshots/` 保存查询条件和脱敏基线。
- `plans/` 保存完整写入 SQL、回滚 SQL、范围和执行状态。
- 这些文件均为本机运行时状态，不得提交到 Git；脚本在所有支持的平台上先收紧文件权限，再执行原子替换。
