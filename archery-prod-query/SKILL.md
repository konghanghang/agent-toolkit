---
name: archery-prod-query
description: 通过 Archery 查 94AI 生产 MySQL, 只读安全工作流, 自动登录刷新 session。凭证 + MFA 写在运行时配置的 ARCHERY_DIR 下，archery_login.py 启 Chrome 自动登录 + 抓 csrftoken/sessionid 写到 ARCHERY_DIR/archery_session.json，archery_query.py 直接用 session 发请求。覆盖场景: 列实例/库/表、读 ai_task / stat_day_base / t_smart_task 等业务表、拆分国内/海外口径、定位权限缺失。
---

# Archery Production Query

Use this skill whenever the next useful step is to pull **production read-only facts** through Archery rather than guessing from code or using direct database credentials.

Prefer the bundled script for repeatable queries. Keep queries read-only and keep the final write-up focused on the user's business ownership boundary instead of platform-wide vanity totals.

## 凭证 & Session 文件

首次使用先初始化运行时配置:

```bash
python3 SKILLS_ROOT/common/bootstrap_94ai.py
```

初始化后统一使用这些运行时变量：

- `WORK_ROOT`: 94AI 工作区根目录
- `SKILLS_ROOT`: 当前共享技能根目录
- `PYTHON_BIN`: 统一 Python 解释器
- `TMP_DIR`: 临时目录
- `ARCHERY_DIR`: Archery 凭证与 session 目录

统一约定:

- 推荐入口命令: `archery` (脚本位置 `SKILLS_ROOT/bin/archery`)
- 首次启用后，在 shell 中通过 `export PATH="<skills-bin-dir>:$PATH"` 暴露命令
- 所有脚本都使用 `PYTHON_BIN` 执行
- 所有依赖都安装到 `PYTHON_BIN` 对应的虚拟环境
- 脚本目录固定为 `SKILLS_ROOT/archery-prod-query/scripts`
- 跨平台稳定入口始终是 `PYTHON_BIN + SKILLS_ROOT/.../scripts/*.py`

推荐优先使用短命令:

```bash
archery login
archery list-instances
archery list-databases --scope domestic
archery list-databases --scope overseas
archery query --module sms --database "short_link" --sql "select 1"
```

底层 Python 脚本仍然保留，方便排查或单独调试。

凭证文件 (不进 git, 初始化时配置目录): `ARCHERY_DIR/archery-credentials.json`

```json
{
  "username": "<your-archery-username>",
  "password": "xxx",
  "mfa_secret": "BASE32_TOTP_SEED",
  "base_url": "https://archery-k8s.94ai.pro",
  "login_path": "/login/",
  "target_url": "https://archery-k8s.94ai.pro/sqlquery/"
}
```

Session 文件 (由 archery_login.py 自动生成): `ARCHERY_DIR/archery_session.json`

```json
{
  "base_url": "https://archery-k8s.94ai.pro",
  "csrf_token": "...",
  "cookie": "csrftoken=...; sessionid=...; ...",
  "cookies_dict": {"csrftoken": "...", "sessionid": "...", ...},
  "user_agent": "...",
  "url": "https://archery-k8s.94ai.pro/sqlquery/",
  "saved_at": 1750000000.0
}
```

首次安装依赖:

```bash
PYTHON_BIN -m pip install selenium webdriver-manager pyotp requests
```

Fallback: 如果没有 session 文件, `archery_query.py` 会回退到环境变量 `ARCHERY_BASE_URL` / `ARCHERY_CSRF_TOKEN` / `ARCHERY_COOKIE` (进程 → HKCU → HKLM)。**推荐用 session 文件**, 不用手动从浏览器复制 cookie。

## 工作流

### Step 1: 确认 session 是否还有效

```bash
archery list-instances
```

- 拿到实例列表 → session 有效, 跳到 Step 3
- 报 401/403/302 或重定向到登录页 → session 过期, 走 Step 2
- 找不到 `ARCHERY_DIR/archery_session.json` → 首次使用, 走 Step 2

### Step 2: 登录刷新 session (约 30 秒)

```bash
archery login
```

脚本会:
1. 启 Chrome, 访问 `https://archery-k8s.94ai.pro/login/`
2. 自动填账号/密码, 自动填 TOTP 6 位 MFA (`pyotp.TOTP(secret).now()`)
3. 等待跳到主页 `/sqlquery/`
4. 抓 cookies (csrftoken + sessionid) 写到 `ARCHERY_DIR/archery_session.json`
5. 用 `/group/user_all_instances/` 做一次页面 XHR 验证；验证失败时删除无效 session 文件

如需登录后自动关闭浏览器窗口，可直接执行:

```bash
PYTHON_BIN SKILLS_ROOT/archery-prod-query/scripts/archery_login.py --no-keep
```

### Step 3: 查数据

实例 / 库 / 表 / SQL 四级 API 都在 `archery_query.py`:

```powershell
# 列可读实例
archery list-instances

# 列某实例下数据库
archery list-databases --instance "main-db"

# 国内大部分传统业务, 自动补 main-db
archery list-databases --scope domestic

# 海外业务, 自动补 海外生产数据库
archery list-databases --scope overseas

# 国内新业务 (短信/短链/WA/回调/AgentCC), 自动补 polarDB-TiDB
archery list-databases --module agentcc

# 列表
archery list-tables --instance "main-db" --database "ai_admin"

# 查列结构
archery query --instance "log-db" --database "auto_dialer_data" `
  --table "stat_day_base" --sql "show columns from stat_day_base"

# 跑只读聚合 (JSON 输出)
archery query --instance "main-db" --database "ai" --table "ai_task" --format json `
  --sql "select count(distinct flow_id) as smart_flow_cnt from ai_task where create_time >= '2026-01-01' and create_time < '2026-07-01' and flow_id is not null"
```

`archery` 入口会在 session 缺失或明显失效时，自动执行一次 `archery login --no-keep` 再重试原命令。
同时支持轻路由参数:

- `--scope domestic|overseas`
- `--module main|newbiz|sms|short-link|wa|callback|agentcc|overseas`
- `--service <value>`: `--module` 的别名

轻路由只负责自动补 `--instance`，不会自动猜数据库名或 SQL。显式 `--instance` 优先级最高。

## Workflow (业务侧)

1. Confirm the business question first.
   - Decide whether the user needs:
   - table discovery
   - schema inspection
   - one-off counts
   - monthly trends
   - domestic vs overseas split
   - module-specific attribution such as new smart tasks, TikTok Shop outbound, AgentCC
2. Discover readable scope before writing SQL.
   - List readable instances first.
   - Then list databases for the chosen instance.
   - If needed, list tables for the chosen database.
3. Keep SQL read-only.
   - Use `select`, `show`, `desc`, `describe`, and metadata queries only.
   - Never run `insert`, `update`, `delete`, `alter`, `drop`, `truncate`, or DDL through this skill.
4. Separate platform background from personal attribution.
   - Do not write full-platform totals as the user's personal output unless the user explicitly owns that whole platform.
   - Prefer module-scoped data instead of整个平台 vanity totals
   - `stat_day_base` 只能在业务口径已确认时使用
5. Report permission gaps clearly.
   - If Archery returns `Access denied`, stop guessing.
   - Tell the user exactly which instance, database, and table permission is missing.

## 实例选择

Use different instances and keep the wording explicit:

- Common domestic instances: `main-db`, `log-db`
- Common overseas instance: `海外生产数据库`
- 如果历史口径或旧文档写的是 `overseas-production-db`，以当前 Archery 实际实例名为准

Do not merge domestic and overseas numbers unless the user explicitly asks for a combined total.

### 常用实例语义

以下实例名已于 `2026-07-08` 通过 `archery list-instances` 实际确认存在：

- `main-db`
  - 国内业务主库
  - 大部分传统国内业务优先从这里开始找
- `polarDB-TiDB`
  - 国内新业务主库
  - 短信、短链、WA、回调、AgentCC 等新业务优先从这里开始找
- `海外生产数据库`
  - 海外业务主库
  - 海外相关业务优先从这里开始找

使用原则：

- 用户只说“国内大部分业务”，默认优先看 `main-db`
- 用户明确提到“短信 / 短链 / WA / 回调 / AgentCC”等新业务，优先看 `polarDB-TiDB`
- 用户明确提到“海外”，优先看 `海外生产数据库`
- 如果业务归属仍不清楚，先用 `archery list-databases --instance <instance>` 缩小范围，不要凭感觉直接写 SQL

CLI 轻路由对应关系：

- `--scope domestic` -> `main-db`
- `--scope overseas` -> `海外生产数据库`
- `--module sms|short-link|wa|callback|agentcc|newbiz` -> `polarDB-TiDB`
- `--module main` -> `main-db`
- `--module overseas` -> `海外生产数据库`

## 业务口径模板

以下内容不要堆在通用查询流程里，按需去看 `references/metric-patterns.md`：

- New Smart Task
- Legacy Smart Task
- `stat_day_base` 聚合模板
- TikTok Shop / 抖店外呼映射路径
- 分页取 ID 再聚合的 SQL 模板
- 统计口径和输出措辞模板

## Output Rules

When answering the user after querying:

1. State the exact statistic scope.
   - Example: `As of 2026-06-21 for task-creation data; as of 2026-06-20 for outbound fact tables`
2. Distinguish:
   - platform background
   - user-owned module data
3. If data is partial because of permissions, say so directly.
4. If June only has data until a mid-month date, mark it as partial month.

## Permission Diagnosis

Treat these as separate checks:

1. instance readable
2. database readable
3. table readable
4. SQL executable

Typical examples:

- instance readable, table denied:
  - ask for `instance.database.table` permission
- database denied:
  - ask for `instance.database` permission
- business mapping table denied:
  - explain that the metric path is confirmed in code but cannot be executed until permission is granted

## Pagination

Archery `limit_num` only caps the returned row count for one query result. When the mapping table may exceed one page, page explicitly in SQL.

Preferred pattern:

```sql
select distinct target_task_id
from t_smart_task_branch
where ...
order by target_task_id
limit 0, 200;
```

Then continue with:

```sql
limit 200, 200
limit 400, 200
limit 600, 200
```

Stop when the returned row count is less than the page size.

Use pagination whenever:

- the branch or mapping table may exceed 200 rows
- Archery row limits could truncate `task_id` discovery
- you need a complete set of IDs before aggregating in `stat_day_base`

## 常见报错

| 报错 | 原因 | 解决 |
|------|------|------|
| `Missing required environment variable` 之外提示找不到 `ARCHERY_DIR/archery_session.json` | 首次使用 / session 文件被删 | 跑 `archery_login.py` |
| HTTP 401 / 403 / 302 重定向到 `/login/` | session 过期 | 重跑 `archery_login.py` (脚本会自动提示) |
| `csrftoken=''` 或 CSRF 校验失败 | 登录后没抓到 csrftoken cookie | 重跑 `archery_login.py`, 确认登录成功 |
| MFA 输入框未找到 / TOTP 提交 disabled | TOTP 已过期 / 30s 窗口剩 <5s | 重跑 (脚本会主动等到新窗口) |
| `StaleElementReferenceException` | 页面重渲染 | 已用 `with_element` 重试, 不应再出现 |
| `Access denied for table ...` | 实例/库/表权限缺失 | 找 DBA 开权限, 不要绕过 |
| Chrome 启动失败 | driver 不匹配 | `PYTHON_BIN -m pip install -U webdriver-manager` |

## Archery 2FA 页面踩坑

Archery 的 2FA 验证页 (`/login/2fa/`) 有几个非标设计, `archery_login.py` 已经做了专门处理:

1. **"验证" 按钮 id=`btnAuth`**, `type=button` (不是 submit!), class=`btn btn-success btn-block`
   - 普通 `button[type='submit']` selector 找不到它, 已加 `button#btnAuth` / `button.btn-success` 优先匹配
2. 页面上有 `'Google身份验证器'` tab 文字, `contains('验证')` 会误匹配它
   - 已用 id 精确锁定, 不依赖文字匹配
3. TOTP 输入框填值后, 按钮 disabled 状态不会自动切换
   - 必须 JS `dispatchEvent` 触发 `input` + `change` + `keyup` + `keydown` 事件
   - 单纯 `send_keys` 不触发 React/Vue onChange, 已强制走 JS 兜底
4. Archery 后端用 Django, cookies 共 3 个: `sessionid` (HttpOnly) + `csrftoken` + `acw_tc` (阿里云 WAF)
   - `sessionid` 是 Django session key, 必须带
   - `csrftoken` 同时要放 Cookie header 和 X-CSRFToken header
   - `acw_tc` 是阿里云 WAF 给的, 不带会被识别为机器人

## Session 有效性

- 跑一次 `archery_login.py` 写入的 `ARCHERY_DIR/archery_session.json` 通常能用 **数小时到 1 天** (Archery 默认 session 配置)
- `archery_query.py` 启动时会打印 `age=Xmin`, 超过 11 小时会标 ⚠ 提示重登
- 旧 session 失效时, requests 会收到 302 重定向到 `/login/`, 这时 `_parse_json` 会直接报错并提示跑 `archery_login.py`

## 脚本

- `scripts/archery_login.py` — 启 Chrome 自动登录 + MFA + 抓 session (写 `ARCHERY_DIR/archery_session.json`)
- `scripts/archery_query.py` — 用 session 直接 POST 查数据 (不依赖浏览器)
- `references/metric-patterns.md` — 业务查询模式 + SQL 模板

用 `PYTHON_BIN <script> --help` 查看完整参数。

## Reference

Read `references/metric-patterns.md` when you need example SQL templates and wording patterns for:

- new smart tasks
- domestic / overseas monthly trends
- `stat_day_base`
- permission request wording
