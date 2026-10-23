# SLS 目标与日志保留期配置

在 `~/.local/agent-toolkit/work/config.json` 的 `sls` 节配置真实值；可用 `AGENT_TOOLKIT_WORK_CONFIG` 指定其他私有文件。初始化方式见[工作环境私有配置](../../../docs/installation.md#工作环境私有配置)。以下名称均为虚构示例，必须替换为自己的 project、logstore 和服务名。

## 只有一套日志资源

只有一组 project/logstore 时，不需要填写国内、海外或任何环境名称：

```json
{
  "schema_version": 1,
  "sls": {
    "retention_days": 7,
    "default": {
      "project": "example-project",
      "logstore": "example-logstore"
    },
    "services": {}
  }
}
```

`retention_days` 填该日志库实际配置的保留天数，示例的 `7` 不是默认值。服务共用此目标时，无须逐个登记：

```bash
sls search-safe --service example-api --keyword ERROR
sls agg-safe --service example-api --sql 'SELECT count(*) AS c'
```

## 有国内、海外或其他环境

多个环境使用 `scopes`。下面示例的国内和海外日志均保留 3 天；实际天数可分别调整。`default_scope` 表示省略 `--scope` 时使用的环境，若不希望默认选择环境，可以省略此字段。

```json
{
  "schema_version": 1,
  "sls": {
    "default_scope": "domestic",
    "scopes": {
      "domestic": {
        "retention_days": 3,
        "default": {
          "project": "example-cn-project",
          "logstore": "example-cn-logstore"
        },
        "services": {
          "example-api": {
            "project": "example-api-project",
            "logstore": "example-api-logstore",
            "retention_days": 7
          }
        }
      },
      "overseas": {
        "retention_days": 3,
        "default": {
          "project": "example-overseas-project",
          "logstore": "example-overseas-logstore"
        },
        "services": {}
      }
    }
  }
}
```

```bash
sls search-safe --scope domestic --service example-data-service --keyword ERROR
sls search-safe --scope overseas --service example-agent-service --keyword ERROR
```

`domestic`、`overseas` 是环境名称，可以换成 `production`、`staging` 等名称，并在 `--scope` 中使用对应名称。只有一个具名环境时也可以省略 `--scope`。`cn`、`intl`、`global` 为兼容旧命令而保留的环境别名。

## 字段与选择规则

| 字段 | 用途 |
|---|---|
| `default.project`、`default.logstore` | 该范围默认查询的日志目标，必须填写非空字符串 |
| `services.<服务名>` | 该服务专属的 project/logstore，优先于所选范围的默认目标 |
| `retention_days` | 该范围的日志保留天数，填写正整数；缺省或 `null` 表示未知 |
| `services.<服务名>.retention_days` | 该服务目标的保留天数；省略时继承所在范围，显式 `null` 表示未知 |
| `default_scope` | 多环境时的默认环境，必须是已配置名称 |

选择路由时，显式 `--scope` 优先；省略时可使用明确配置的默认环境或唯一环境。没有默认环境且配置了多个环境时，只有服务路由能唯一确定目标才自动选择，否则停止并提示补充 `--scope`。查询文本中只匹配一个已配置服务时，也可据此推断服务；多匹配时必须明确指定服务。

单目标格式与 `scopes` 格式二选一，不混用。普通查询、聚合及两个受限入口读取同一配置，每次命令重新读取，修改后下次运行生效。

## 保留期如何影响查询

- 已知保留天数时，入口按当前时间计算可查询边界。查询窗口部分超出保留期时，自动缩小到仍在保留期内的部分，并在标准错误说明未覆盖的区间。
- 整个窗口已过期时，在登录和请求 SLS 前停止。已过期日志无法通过扩大时间窗口或 pSQL 恢复。
- 保留天数未知时，入口明确提示未知，不擅自使用 3 天。应先在阿里云控制台确认对应 logstore 的实际保留设置，再更新本机配置。
- `0`、负数、小数、布尔值和字符串都不是有效天数；不要用 `0` 表示永久保留或未知。
- 自动边界基于本机配置，工具不会修改阿里云保留策略，也不能证明服务端一定保有整个窗口内的数据。配置应与控制台保持一致。
- 超出保留期或保留期未知时，返回 0 条都不能用于否定历史上发生过的调用、异常或状态变更。需要更早的数据时，应使用所在环境实际提供的审计或长期存储。

**日志保留期与受限入口的单次查询窗口上限是两回事。** `search-safe` / `agg-safe` 仍限制每次最多 3 天、100 行，这是查询范围限制，不代表日志只保存 3 天。例如保留 7 天时，可用 `--from` / `--to` 查询第 5 天内的一小时。

普通 `query` / `agg` 完整指定 `--project` 和 `--logstore` 时不读取路由配置，保留期视为未知；应自行核对目标及保留设置。受限入口继续禁止自选 project/logstore。

## 旧配置兼容

已有 `sls.domestic`、`sls.overseas` 格式可以继续使用，各节同样支持 `retention_days` 和服务覆盖。旧格式配置了国内环境时保留其默认选择行为；只配置一个海外环境时可自动选择该唯一环境。

升级旧配置时，应显式补上实际保留天数，例如在对应范围中填写 `"retention_days": 3`；缺少该字段会提示未知，不会自动假定 3 天。初始化脚本不会覆盖已有文件或自动填充保留期。

迁移到新格式时，把国内、海外配置移入 `sls.scopes`，如需默认国内则设置 `"default_scope": "domestic"`。不要同时保留旧范围字段和新 `scopes`。
