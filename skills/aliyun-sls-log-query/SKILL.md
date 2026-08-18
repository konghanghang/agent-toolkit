---
name: aliyun-sls-log-query
description: 查询阿里云 SLS 日志服务 (Log Service), 用于排查 data-center / AgentCC / work 等微服务的线上日志。自动登录 RAM 用户 (账号+密码+VMFA TOTP 自动), 抓 cookies + 真实 x-csrf-token, 然后用 requests 直接 POST getLogs.json 取日志, 不依赖浏览器。当用户提到 SLS、阿里云日志、example-domestic-project、lognext、查日志、trace ID、请求链路、线上错误、data-center 服务、AgentccController、ai-admin 等场景, 或需要在没有 JumpServer / kubectl 时查生产日志, 使用本技能。
---

# 阿里云 SLS 日志查询

## 适用场景

- 用户问 "查一下 xxx 服务的日志"
- 提供 trace ID / 请求链路 / order ID / phone 后缀, 要定位错误
- 问 "线上有没有报 ERROR" / "xxx 时间点发生了什么"
- 直接给 SLS URL: `sls.console.aliyun.com/lognext/project/.../logsearch/...`
- JumpServer / kubectl 不可用, 但需要看生产日志
- 需要按时间窗口批量取日志做分析

## 前置准备

首次使用先初始化运行时配置:

```bash
python3 "$AGENT_TOOLKIT_ROOT/common/bootstrap_work.py"
```

初始化后统一使用这些运行时变量：

- `WORK_ROOT`: work 工作区根目录
- `AGENT_TOOLKIT_ROOT`: 当前共享技能根目录
- `PYTHON_BIN`: 统一 Python 解释器
- `TMP_DIR`: 临时目录
- `SLS_DIR`: SLS 凭证与 session 目录

凭证文件路径: `SLS_DIR/aliyun-credentials.json` (不进 git, 初始化时配置)

统一约定:

- 推荐入口命令: `sls`（脚本位置 `$AGENT_TOOLKIT_ROOT/bin/sls`）
- 首次启用后，在 shell 中通过 `export PATH="<skills-bin-dir>:$PATH"` 暴露命令
- 所有脚本都使用 `PYTHON_BIN` 执行
- 所有依赖都安装到 `PYTHON_BIN` 对应的虚拟环境里
- 脚本目录固定为 `$AGENT_TOOLKIT_ROOT/skills/aliyun-sls-log-query/scripts`
- 跨平台稳定入口始终是 `PYTHON_BIN + AGENT_TOOLKIT_ROOT/skills/.../scripts/*.py`
- 需要导出日志时，先读取运行时配置的 `TMP_DIR`，使用当前平台的路径 API 拼接文件名
- 所有临时文件统一写到 `TMP_DIR`，不要落到当前项目目录，也不要硬编码某个平台的临时根目录
- 如果当前执行环境的沙箱拦截 `TMP_DIR` 写入，直接申请该目录的权限，不回退到项目目录

推荐优先使用短命令:

```bash
sls login
sls search-safe --service ai-open-api --size 1
sls search-safe --scope domestic --service data-center --trace-id 6b62e9b764de5d45
sls search-safe --scope overseas --service agentcc --keyword ERROR
```

Windows PowerShell 中优先调用初始化生成的公共 `.cmd` 入口，不直接拼接底层 Python 脚本：

```powershell
$AgentToolkitRoot = (Resolve-Path (Join-Path $HOME ".agent-toolkit")).Path
& "$AgentToolkitRoot\bin\sls.cmd" login
& "$AgentToolkitRoot\bin\sls.cmd" search-safe --service ai-open-api --size 1
```

Codex 日常排查优先使用 `search-safe` / `agg-safe`。这两个入口只允许内置路由，禁止 `--project`、`--logstore`、`--out`、`--full`，查询窗口最多 3 天、返回最多 100 行；`agg-safe` 还强制使用单条 SELECT 聚合，禁止直接输出或分组原始日志字段，只允许聚合值及有边界的 `regexp_extract(...)` 派生维度。需要完整日志落盘、自选 project/logstore 或非聚合 pSQL 时才使用原始 `query` / `agg`，并保留审批。

底层 Python 脚本仍然保留，方便排查或单独调试。

格式:
```json
{
  "username": "<your-ram-subaccount>@<your-account-alias>.onaliyun.com",
  "password": "xxx",
  "mfa_secret": "BASE32_TOTP_SEED",
  "target_url": "https://sls.console.aliyun.com/lognext/project/<project>/logsearch/<logstore>",
  "project": "example-domestic-project",
  "logstore": "datalogs",
  "default_query": "data-center | with_pack_meta"
}
```

首次安装依赖:

```bash
PYTHON_BIN -m pip install selenium webdriver-manager pyotp requests
```

## 工作流

### 第 1 步：确认 session 是否还有效

```bash
sls query --service ai-open-api --size 1
```

- 拿到日志 → session 有效, 直接进入 Step 3
- 报 `code: ConsoleNeedLogin` / `Invalid CSRF token` → session 过期, 走 Step 2
- 报找不到 `SLS_DIR/aliyun_session.json` → 首次使用, 走 Step 2

### 第 2 步：登录刷新 session（约 60 秒）

```bash
sls login
```

登录成功并验证 session 后会自动关闭浏览器。只有需要人工调试登录页时才保留窗口:

```bash
sls login --keep
```

脚本会:
1. 启动 Chrome, 自动填账号/密码
2. TOTP 自动算 6 位 MFA 码 (`pyotp.TOTP(secret).now()`)
3. 自动跳过 "重置密码" 弹窗
4. 跳到 `target_url`, 抓 cookies + 真实 `x-csrf-token` (从页面 XHR 偷) 写到 `SLS_DIR/aliyun_session.json`

**关键技巧**: `x-csrf-token` 不在 cookie 也不是简单 JS 变量, 是页面 axios 拦截器动态加的。脚本通过 CDP `goog:loggingPrefs: performance` 偷页面自己发的 XHR (`Network.requestWillBeSent` 事件) 抓出真实 header 值。

### 第 3 步：查日志

```bash
# 国内 ai-open-api
sls query --service ai-open-api

# 国内其他服务
sls query --scope domestic --service data-center --minutes 60 --size 100

# 海外服务
sls query --scope overseas --service agentcc --minutes 60 --size 100

# 按 trace ID
sls query --scope domestic --service data-center --trace-id 6b62e9b764de5d45

# 按关键词
sls query --scope domestic --service data-center --keyword ERROR

# 自定义 SLS 查询语法
sls query --scope domestic --service data-center --query 'data-center and ERROR and "OrderService" | with_pack_meta'

# 指定时间窗口 (Unix 时间戳)
sls query --scope overseas --service agentcc --from 1783392000 --to 1783395000

# 完整内容不截断 + 落盘
sls query --scope domestic --service data-center --full --out "<TMP_DIR>/logs.json"
```

命令中的 `<TMP_DIR>` 是运行时配置值的占位符；执行前必须替换成实际路径，不要把占位符原样传给命令。

`sls` 入口支持自动路由参数:

- `--scope domestic|overseas`
- `--service <service>`
- `--project <project>` / `--logstore <logstore>` 直接指定

`--trace-id`、`--keyword` 会与 `--query` 正确组合；若 `--query` 或 `default_query` 末尾带 `| with_pack_meta`，脚本会自动保持该后缀在最后。

### 第 4 步：切换 project / logstore

项目路由规则:

- 海外所有服务 → `example-overseas-project / datalogs`
- 国内 `ai-open-api` → `example-api-project / example-api-project`
- 国内其他服务 → `example-domestic-project / datalogs`

如果信息不足以确定是“海外”还是“国内其他服务”，必须先追问确认，再查日志。

要手工指定别的 project / logstore:

```bash
sls query --project <other-project> --logstore <other-logstore> --query '*'
```

切回默认: 直接不带这俩参数即可。

## 工作流建议

### 按 Trace ID 排查

```
1. 用户提供 trace ID (16 hex 字符)
2. 先判断路由:
   - 海外 -> sls query --scope overseas --service <service> --trace-id <id> --minutes 60 --size 50 --full --out "<TMP_DIR>/trace.json"
   - 国内 ai-open-api -> sls query --service ai-open-api --trace-id <id> --minutes 60 --size 50 --full --out "<TMP_DIR>/trace.json"
   - 国内其他服务 -> sls query --scope domestic --service <service> --trace-id <id> --minutes 60 --size 50 --full --out "<TMP_DIR>/trace.json"
3. 解析 `<TMP_DIR>/trace.json`: 按 span 顺序看 SPRING_REQ → OKHTTP_REQ → OKHTTP_RESP → SPRING_RESP
4. 找 ERROR / Exception / 慢调用 (PerfLog 字段里有耗时)
5. 跨服务: 同一 trace ID 在不同服务里的 span, 拼出完整调用链
```

### 日志保留期硬门禁

- 当前国内 SLS 日志仅保留最近 3 天。每次查询前必须按当前时间核对请求窗口和实际可查询区间。
- 查询开始时间早于保留边界时，只查询仍在保留期内的部分，并明确说明无法覆盖的历史区间。
- 超出保留期的区间返回 0 条，不代表历史上没有日志；禁止据此排除接口调用、异常、状态变更或操作行为。
- 需要追查 3 天以前的事实时，改用 ClickHouse 操作日志、数据库审计、MySQL binlog 或其他长期存储，不能继续扩大 SLS 时间窗口。

### 时间窗口策略

- 不知道时间: 先 `--minutes 60 --size 20` 探一次, 看日志最新时间戳
- 知道大致时间: 先确认时间位于最近 3 天保留期内，再用 `--from`/`--to` 精确窗口, 避免漏掉
- 排查偶发问题: `--minutes 1440 --size 500 --out "<TMP_DIR>/day.json"` 拉全天再筛选

### 关键词组合

SLS 查询语法 (类似 Kibana KQL, 但有差异):
- `data-center` — 必含 `data-center` 的日志 (服务名)
- `ERROR and "OrderService"` — ERROR 且包含 OrderService
- `not DEBUG` — 排除 DEBUG 级别
- `__tag__:_namespace_ = "default"` — 按 K8S namespace 过滤
- `* | with_pack_meta` — `*` 是查询, `with_pack_meta` 是把同秒日志合并显示

默认 `default_query` 是 `data-center | with_pack_meta` — 查 work data-center 服务的全部日志。

### 聚合统计与精确总数（pSql）

`sls query` 默认 `pSql=false`，**做不了 count / GROUP BY / 精确总数**（`Size` 上限 100，`meta.count` 是返回行数不是命中数）。需要"今天多少条 ERROR""按任务分组计数""精确去重总数""列表 >100 条"时，用 pSql 自己发请求（复用 skill session），**可靠模式与避坑见 [references/advanced-query.md](references/advanced-query.md)**：

- 中文/纯数字 token 一律用 `like '%...%'`，别用 index `"中文"`/`"606332"`
- 精确去重总数用 `count(distinct case when ...)` 且**无 GROUP BY**
- 分组先 index 限定子集再 GROUP BY + HAVING；全表 GROUP BY 不稳定
- 列表 >100 条 → 分时段查询合并去重
- **查不到 ≠ 没有**：换口径（like vs index、count vs GROUP BY）交叉验证再下结论

## 查询路由判断

对话中默认按下面规则路由:

1. 用户明确说“海外”“国际”“新加坡”等海外语义 → `example-overseas-project / datalogs`
2. 用户明确要查 `ai-open-api` → `example-api-project / example-api-project`
3. 用户明确是国内且服务不是 `ai-open-api` → `example-domestic-project / datalogs`
4. 如果只有 trace ID / 关键词 / 报错片段，仍然无法判断是海外还是国内其他服务，必须先追问确认 project，再继续查询

凭证文件里的 `project` / `logstore` 只是登录落点和底层脚本默认值，不作为对话查询路由的唯一依据。

## 常见报错

| 报错 | 原因 | 解决 |
|------|------|------|
| `ConsoleNeedLogin` / `Invalid CSRF token` | session 过期 | 直接重跑 `sls query ...`，入口会自动登录、关闭浏览器并重试一次 |
| 找不到 `SLS_DIR/aliyun_session.json` | 首次使用 | 先执行 `sls login`，或者直接执行一次 `sls query ...` |
| 找不到 `SLS_DIR/aliyun-credentials.json` | 凭证未配 | 按 "前置准备" 创建 |
| MFA 提交后页面没反应 | TOTP 已过期 | 重新跑 (脚本会等新 30s 窗口) |
| `StaleElementReferenceException` | 页面重渲染过快 | 已用 `with_element` 重试, 不应再出现 |
| Chrome 启动失败 | driver 不匹配 | `PYTHON_BIN -m pip install -U webdriver-manager` |

## 已知信息

- 使用自己的 work 阿里云 RAM 子账号及对应主账号 alias
- MFA 为 VMFA, TOTP 标准 6 位 30 秒
- 登录后可能出现“重置密码”弹窗, 需要点“跳过重置”
- 默认查询路由:
  - 海外服务 -> `example-overseas-project / datalogs`
  - 国内 `ai-open-api` -> `example-api-project / example-api-project`
  - 国内其他服务 -> `example-domestic-project / datalogs`
- 常见服务名: `data-center`, `ai-admin`, `ai-decision-system`, `company-aggre`, `call-task`, `agentcc`
- 如需保留抓包或请求样例, 统一放共享仓库内的脱敏文件, 不要引用个人机器路径

## 脚本

- `$AGENT_TOOLKIT_ROOT/bin/sls` — 推荐短命令入口，封装 login/query 和 session 自动补登录
- `scripts/aliyun_login.py` — 启 Chrome 自动登录 + 抓 session
- `scripts/aliyun_sls_query.py` — 直接 POST 查日志 (不依赖浏览器)

## 临时文件约定

- `<TMP_DIR>` 表示 `~/.local/agent-toolkit/work/runtime.json` 中 `tmp_dir` 的实际值；通过 `pathlib.Path` 或当前平台等价的路径 API 拼接文件名
- 对话执行中需要导出 JSON、trace、day 级别结果时，统一写入 `TMP_DIR`
- 不要把临时日志结果写进当前项目目录
- 如果当前运行环境限制 `TMP_DIR` 写入，先申请该目录的权限，再继续执行

用 `PYTHON_BIN <script> --help` 查看完整参数。
