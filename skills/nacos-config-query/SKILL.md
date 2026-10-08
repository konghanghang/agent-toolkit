---
name: nacos-config-query
description: '只读查询工作环境国内或海外 Nacos 配置。用于按服务名读取 yaml、对比 cn 与 os、读取共享配置，以及查询 LANGUAGE_GROUP 翻译。不要用于修改 Nacos、讲代码逻辑、查测试环境配置，或按配置内容反查文件名。'
---

# Nacos 配置查询

通过只读 HTTP 包装按精确 dataId 读取国内或海外配置。这不是 Nacos 控制台，也不提供按内容搜索。

## 安全边界

- 只调用 `scripts/nacos_query.py` 的 `get` 和 `lang`。当前工具只提供精确 dataId 读取，不请求 `/search`，不使用 `pattern=*` 或其他模糊模式。
- 从本机工作运行时配置读取 `nacos_cn_base_url` 和 `nacos_os_base_url`。不要在 Skill、脚本、测试或最终答复中写入生产域名。
- 当前包装不使用用户名、密码、Token、Cookie 或 Session。地址必须是不带认证信息、query string 和 fragment 的 http(s) URL。
- 脚本会打码常见密钥字段、URL 查询参数中的口令、JSON 密钥和 YAML 多行块。回答里不要复述明文密钥；打码结果里的 `***` 保持原样。
- 退出码 0 表示请求的范围都读到了配置。退出码 1 表示至少一个范围 404、5xx 或请求失败，JSON 里仍有 `ok` 和 `hint`。退出码 2 表示参数或运行时配置不完整。
- 404 只说明这份包装没有该 dataId。停止在这个 dataId，不要改用模糊文件名再查。

## 前置配置

`nacos_cn_base_url` 和 `nacos_os_base_url` 对工具箱整体是可选项，但查询对应范围前必须配置。不要猜测、扫描或从其他文件复制地址。仅在运行时配置缺失或目标范围没有地址时，交互运行：

```bash
python3 "$AGENT_TOOLKIT_ROOT/common/bootstrap_work.py"
```

初始化把地址写入本机 `~/.local/agent-toolkit/work/runtime.json`，该文件不进入 Git。已有其他配置时保留提示中的默认值。

```bash
"$AGENT_TOOLKIT_ROOT/.venv/bin/python" \
  "$AGENT_TOOLKIT_ROOT/skills/nacos-config-query/scripts/nacos_query.py" --help
```

Windows PowerShell 不假定 `PYTHON_BIN` 已导出为环境变量；从本机运行时配置读取真实解释器和工具包路径，再用调用运算符执行：

```powershell
$RuntimeFile = Join-Path $HOME ".local\agent-toolkit\work\runtime.json"
$Runtime = Get-Content -LiteralPath $RuntimeFile -Raw | ConvertFrom-Json
$PythonBin = [string]$Runtime.python_bin
$AgentToolkitRoot = [string]$Runtime.agent_toolkit_root
& "$PythonBin" "$AgentToolkitRoot\skills\nacos-config-query\scripts\nacos_query.py" --help
```

## 命名规则

Spring Cloud Nacos 的 `file-extension` 为 `yaml`。普通服务的 dataId 是 `{spring.application.name}.yaml`，组是 `DEFAULT_GROUP`，请求不带 `group`。

`--service` 会把服务名规范成小写并把 `_` 换成 `-`。没有后缀时补 `.yaml`，`.yml` 会改成 `.yaml`。已经是 `.properties` 的名字保持原样。

```bash
"$AGENT_TOOLKIT_ROOT/.venv/bin/python" \
  "$AGENT_TOOLKIT_ROOT/skills/nacos-config-query/scripts/nacos_query.py" \
  get --scope cn --service example-service
```

`--data-id` 覆盖 `--service`，除翻译文件的 locale 别名外按原样请求。共享配置从目标项目 bootstrap 的 `sharedConfigs` 等真实声明取得，使用精确 `--data-id` 读取，不维护组织内部共享配置文件名清单，也不把它们当成服务名。

```bash
"$AGENT_TOOLKIT_ROOT/.venv/bin/python" \
  "$AGENT_TOOLKIT_ROOT/skills/nacos-config-query/scripts/nacos_query.py" \
  get --scope cn --data-id example-shared.yaml
```

如果项目存在无后缀或 JSON 格式的特殊 dataId，先从实际声明确认，再用 `--data-id` 指定精确值，不猜测后缀。

翻译走 `lang`。组固定为 `LANGUAGE_GROUP`。locale `zh`、`zh-cn`、`cn` 会写成 `zh_CN`；`id` 会写成 `in`。没有 `common-lang.zh.properties`。前缀只接受 `common-lang` 和 `ai-lang`。

```bash
"$AGENT_TOOLKIT_ROOT/.venv/bin/python" \
  "$AGENT_TOOLKIT_ROOT/skills/nacos-config-query/scripts/nacos_query.py" \
  lang --scope both --locale en --contains 'example-label'
```

## 查询步骤

1. 确定范围：`cn`、`os` 或 `both`。用户没说明时用 `cn`。
2. 用用户给出的服务名，或代码里的 `spring.application.name`，执行 `get --service <name>`。
3. 共享配置用 `get --data-id <精确 dataId>`。
4. 翻译只用 `lang`。
5. 大文件加 `--contains`，只看命中的行。`truncated` 或 `hits_truncated` 为 true 时，结论要说明输出被截断。
6. `ok: false` 时报告 `scope`、`data_id`、`group` 和 `hint` 或 `error`。不要换成别的文件名重试。

对比两套环境时：

```bash
"$AGENT_TOOLKIT_ROOT/.venv/bin/python" \
  "$AGENT_TOOLKIT_ROOT/skills/nacos-config-query/scripts/nacos_query.py" \
  get --scope both --service example-service
```

`--scope both` 会分别返回两边的结果。一边失败时，另一边已经读到的内容仍在 JSON 里。

## 结果边界

公共 Skill 不保存某个环境的 dataId 存在性、文件名差异或历史 404/500 快照。按当前请求的结果处理：404 只说明对应包装未返回该精确 dataId，500 表示当前读取失败；都不能据此扩大模糊搜索、扫描其他配置或切换环境。

需要解释配置如何在代码中生效时，再沿当前项目和部署版本验证。配置查询本身到已授权精确 dataId 的结果为止。
