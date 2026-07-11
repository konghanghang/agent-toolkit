---
name: online-issue-triage
description: '生产环境及测试环境问题排查方法论。覆盖三大场景: 1) 技术 trace 排查 (NPE/异常/慢调用) 2) 号码为什么没导入任务 3) 号码在智能任务为什么没流转。先按环境选择测试 JMS/JumpServer 或生产 SLS，再串联本地代码和按需数据库验证。当用户问"这个 trace 是什么报错"、"测试环境日志"、"号码为什么没导入进去"、"号码在智能任务为什么没流转"、"运营反馈号码没收到短信"、"批量号码没成功"等问题时使用本技能。'
---

# 线上问题排查方法论

## 适用场景

这个 skill 用来编排工作环境常见线上排查，不直接替代日志 skill 或数据库 skill。

仅当共享运行时配置不存在时，在交互终端初始化：

```bash
python3 "$AGENT_TOOLKIT_ROOT/common/bootstrap_work.py"
```

`bootstrap_work.py` 是交互脚本；已有有效运行时配置时不要重复执行，也不要在无 stdin 的非交互命令中直接运行。

初始化后再使用下层能力：

- `sls`
- `archery`
- `jms`

覆盖三类问题：

1. 技术 trace 排查
   - 例如：NPE、异常栈、慢调用、接口报错
2. 号码为什么没导入任务
3. 号码在智能任务里为什么没流转

## 总框架

任何线上问题都按 **现象 → 日志 → 代码 → 数据** 四步走，但顺序取决于问题类型：

- 技术问题：先日志，再代码，再数据库验证
- 业务问题：先数据库看状态，再日志反查时间点，再代码确认分支

三个下层能力分工：

- `aliyun-sls-log-query`
  - 查生产环境日志、异常和调用链
- `jumpserver-k8s-log-query`
  - 查测试环境日志；经 JumpServer 先上 master 定位 NODE，再读对应 worker 的 `/data/logs/test/{service}`
  - 不要为了找目录遍历全部 worker
- `archery-prod-query`
  - 查生产只读业务数据、状态字段、权限边界
- 本地代码定位
  - 在初始化配置的 `WORK_ROOT` 下按具体项目目录用 `rg` 缩小搜索范围

## 路由规则

先判断环境：

- 测试环境：使用 `jumpserver-k8s-log-query`；即使给了 trace ID 或 ERROR，也不切到 SLS
- 生产环境：使用 `aliyun-sls-log-query`，再按部署区和服务选择 project
- 环境不明确：先确认环境；trace ID 本身不能决定日志来源

### SLS 路由

确认是生产环境后，再按本机工作配置 `sls` 节选择查询目标；具体资源名不写入仓库：

- 海外服务：使用 `sls.overseas` 中的服务覆盖或默认路由，命令形态为 `sls query --scope overseas --service <service> ...`。
- 国内已配置服务：使用 `sls.domestic.services.<service>`，命令形态为 `sls query --service <service> ...`。
- 国内其他服务：使用 `sls.domestic.default`，命令形态为 `sls query --scope domestic --service <service> ...`。
- 配置缺失或路由不完整时，按 `aliyun-sls-log-query` 的配置说明准备本机私有配置，不猜测或改写服务端资源名称。

如果用户只给 trace ID、号码、报错片段，仍不足以判断环境和 project，必须先追问，不要盲切。

### Archery 实例路由

实例与业务范围从本机配置、授权元数据和项目说明确认，不使用公共文档中的固定组织映射：

```bash
archery list-instances-safe
archery list-databases-safe --instance "<已确认实例>"
archery list-tables-safe --instance "<已确认实例>" --database "<已确认数据库>"
```

环境、服务或实例归属不明确时先补信息。不要把传统/新业务、国内/海外等标签当作足够的实例证据，也不凭感觉选择历史库或镜像。

## 代码定位规则

- 当前本地代码根目录由运行时配置里的 `WORK_ROOT` 决定
- 先按项目缩小目录，再用 `rg`
- 不要直接在整个 `WORK_ROOT` 上做全量 `grep` / `find`

常见项目路径参考看：

- `references/service-mapping.md`

## 场景分流

命中不同场景时，继续读对应参考文件：

- 技术 trace 排查
  - `references/trace-triage.md`
- 号码为什么没导入任务
  - `references/number-not-imported.md`
- 号码在智能任务为什么没流转
  - `references/smart-task-not-flowing.md`

通用表脑图和代码/服务映射：

- `references/intake-template.md`
- `references/table-cheatsheet.md`
- `references/service-mapping.md`

## 排查提问模板

信息不足时，不要直接乱查，也不要一口气问十个问题。

原则：

- 先问 1 到 3 个最小问题
- 优先补足“路由信息”和“主键 ID”
- 能决定 SLS project / Archery 实例的问题优先级最高

具体提问模板看：

- `references/intake-template.md`

## 输出边界

- 排查过程中允许使用原始业务标识进行精确检索
  - 手机号、taskId、flowDataId、requestId、smsId 可以按原值查
- 最终用户可见输出默认保留定位所需的最小字段
  - 手机号默认轻脱敏
  - 不默认外显 appSecret、cookie、token、完整回调 body
- 如果用户明确要求保留原值继续联调，再按需要输出

## 输出规范

排查结论至少包含：

1. 根因
2. 链路时间线
3. 关键信息
   - 应用、服务、MQ topic、业务 ID、异常类型
4. 代码位置
5. 业务影响
6. 修复 / 下一步建议

标准骨架和示例看：

- `references/conclusion-template.md`

## 参考文件

- `references/conclusion-template.md`
- `references/intake-template.md`
- `references/trace-triage.md`
- `references/number-not-imported.md`
- `references/smart-task-not-flowing.md`
- `references/service-mapping.md`
- `references/table-cheatsheet.md`
