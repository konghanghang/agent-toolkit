---
name: archery-prod-query
description: 通过 Archery 只读查询生产 MySQL 与 ClickHouse，支持元数据发现、受限业务查询、审计分析、权限诊断和自动刷新登录会话。真实地址与凭证保存在本机私有配置；查询生产日志、事实、历史或大表行数据前，必须先展示范围和完整 SQL 并获得明确确认。
---

# Archery 生产库查询

需要通过 Archery 取得生产只读事实时使用。先确认环境、实例和业务范围，再查元数据、起草 SQL 和解释结果；不使用公共文档中的虚构对象作为实际查询目标。

## 生产大表安全门

日志、事实、历史、月表、归档表和无法预估数据量的业务表，默认按线上大表处理。读取这些表的行数据前，先展示：

1. 精确的 `instance.database.table`。
2. 完整 SQL。
3. 租户、任务、主键和时间等过滤条件。
4. 预计返回范围及可能扫描的数据量。

只有用户针对该范围明确确认后才能执行。泛泛的“看一下”不能代替具体大表查询授权，不先查再补解释。

无需额外确认的元数据操作包括实例、数据库、表列表，以及 `SHOW COLUMNS`、`DESC`、`SHOW CREATE TABLE`。不能把元数据发现升级为 `count(*)`、`DISTINCT`、聚合或时间扫描。

权限不足时停止并报告，不自动切换其他环境、历史库、镜像、视图或归档表。

## 本机配置

仅在运行时尚未准备时初始化：

```bash
python3 "$AGENT_TOOLKIT_ROOT/common/bootstrap_work.py"
```

使用运行时中的 `PYTHON_BIN`、`AGENT_TOOLKIT_ROOT`、`TMP_DIR` 和 `ARCHERY_DIR`。真实地址、账号、MFA、Token、Cookie 和 Session 只保存在本机，不写入仓库或回复。

凭证文件为 `ARCHERY_DIR/archery-credentials.json`。`base_url` 必须显式填写；示例域名不可直接连接。`login_path` 可省略为 `/login/`，`target_url` 可省略并由已配置的地址派生：

```json
{
  "base_url": "https://archery.example.invalid",
  "username": "<本机账号>",
  "password": "<本机密码>",
  "mfa_secret": "<本机 TOTP seed>",
  "login_path": "/login/",
  "target_url": "https://archery.example.invalid/sqlquery/"
}
```

登录脚本生成 `ARCHERY_DIR/archery_session.json`。脚本在读取凭证和写入 Session 前收紧本机权限：macOS/Linux 为 `0600` 文件和 `0700` 目录，Windows 为只允许当前用户、SYSTEM 和内置管理员的受保护 ACL。

直接运行底层查询脚本时，缺少 Session 文件可按脚本支持的方式提供 `ARCHERY_BASE_URL`、`ARCHERY_CSRF_TOKEN`、`ARCHERY_COOKIE`；环境变量只留在本机。优先使用登录生成的 Session，而不是把浏览器 Cookie 复制进项目。

## 登录与元数据发现

推荐短命令：

```bash
archery login
archery list-instances-safe
archery list-databases-safe --instance "<已确认实例>"
archery list-tables-safe --instance "<已确认实例>" --database "<已确认数据库>"
```

Windows PowerShell 优先使用公共 `.cmd` 入口：

```powershell
$AgentToolkitRoot = (Resolve-Path (Join-Path $HOME ".agent-toolkit")).Path
& "$AgentToolkitRoot\bin\archery.cmd" login
& "$AgentToolkitRoot\bin\archery.cmd" list-instances-safe
```

三个 `*-safe` 入口只开放实例、数据库和表列表；Session 缺失或明显失效时，入口允许登录刷新并重试一次。原始 `query` 执行任意 SQL 仍保留审批；Rule 的命令允许不替代本 Skill 的范围确认。

实例名、部署区与业务模块之间的映射由本机工作配置的 `archery.scopes`、`archery.modules` 或当前元数据确认。不要根据“传统业务”“新业务”等标签猜固定实例，显式 `--instance` 优先。

## WebDriver 缓存边界

- 保持 `webdriver-manager` 的默认用户级缓存，即 `Path.home() / ".wdm"`；macOS/Linux 常见为 `$HOME/.wdm`，Windows 为 `%USERPROFILE%\.wdm`。
- 禁止设置 `WDM_LOCAL=1` 或 `WDM_LOCAL=true`，不把缓存、ChromeDriver 写入任何 Git 工作区或 Skill 目录。
- 沙箱不能写默认用户缓存时，说明需要的权限或停止登录，不将缓存移进仓库绕过限制。
- 登录后检查 Git 状态；意外生成的运行产物只清理本次范围，保留其他现场。

## 查询步骤

1. 确认用户问题、环境、部署区、租户与统计口径。
2. 用授权元数据入口发现真实实例、库、表和字段。
3. 起草最小只读 SQL；行数据、聚合和大表访问遵守前述范围确认。
4. 执行已确认查询，核对返回行数、截断、分页、时区和数据截止时间。
5. 结合实际代码或业务关系解释结果；权限或数据不完整时保留相应边界。

下面使用虚构对象演示命令形态，不代表执行授权：

```bash
archery query --instance "<已确认实例>" --database "<已确认数据库>" \
  --table "<已确认表>" --sql "show columns from <已确认表>"
```

仅允许 `SELECT`、`SHOW`、`DESC`、`DESCRIBE` 等只读查询。禁止 `INSERT`、`UPDATE`、`DELETE`、`ALTER`、`DROP`、`TRUNCATE` 或任何 DDL。只读并不等于低风险，聚合查询同样需要检查扫描范围。

## 专项方法

- 指标口径、实体与执行量区分、分页关联和虚构 SQL：读 [指标查询模式](references/metric-patterns.md)。
- 审计字段、同环境账号映射、操作统计与零结果边界：读 [审计查询模式](references/audit-log-patterns.md)。

这些参考资料不维护当前组织的实例和表清单。示例字段只用于表达方法，必须用现行 schema 和写入链路核实。

## 输出与权限诊断

写明精确时间区间、时区、统计对象、数据截止时间，以及结果完整性。区分用户模块与平台总体，不将部分月份或缺少关联权限的结果表述为完整指标。

把实例、数据库、表和具体 SQL 权限分别诊断。缺少权限时给出准确对象与所需能力，不自动查替代表、其他租户或其他部署区。审计日志零行不能证明没有 HTTP 请求。

## 故障边界

| 现象 | 处理 |
|---|---|
| 配置缺失或 base_url 为空 | 在本机填写凭证配置；不猜服务地址 |
| HTTP 401/403/302 或跳到登录页 | 按认证失败处理，重新登录 |
| CSRF 或 MFA 失败 | 核对会话、时钟、页面与配置；不输出密钥 |
| 页面与当前选择器不兼容 | 记录版本与缺失能力，停止自动化，不假定其他部署相同 |
| `Access denied` | 报告精确权限缺口，不绕过 |
| Chrome/driver 不匹配 | 说明环境问题，按授权准备依赖，不把安装成功当查询成功 |
| 返回截断或分页未完成 | 明确部分结果，不能声称全量 |

Session 有效期以服务端响应为准；文件年龄只是提示，不能证明认证有效。异常日志和无效 Session 也不能作为零结果。

## 资源

- `scripts/archery_login.py`：Chrome 登录、MFA 和 Session 获取。
- `scripts/archery_query.py`：通过会话执行只读请求。
- `references/metric-patterns.md`：通用指标方法。
- `references/audit-log-patterns.md`：通用审计方法。

用 `PYTHON_BIN <script> --help` 查看实际支持参数。
