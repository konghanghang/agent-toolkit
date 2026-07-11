---
name: online-issue-triage
description: '94AI 线上问题排查方法论。覆盖三大场景: 1) 技术 trace 排查 (NPE/异常/慢调用) 2) 号码为什么没导入任务 3) 号码在智能任务为什么没流转。按场景串联 SLS 日志查询 (aliyun-sls-log-query)、本地代码定位、Archery 数据库查询 (archery-prod-query) 三个工具。当用户问"这个 trace 是什么报错"、"号码为什么没导入进去"、"号码在智能任务为什么没流转"、"运营反馈号码没收到短信"、"批量号码没成功"等线上问题时使用本技能。'
---

# 线上问题排查方法论

## 适用场景

这个 skill 用来编排 94AI 常见线上排查，不直接替代日志 skill 或数据库 skill。

首次使用前，先完成共享运行时初始化：

```bash
python3 "$AGENT_TOOLKIT_ROOT/common/bootstrap_94ai.py"
```

初始化后再使用下层能力：

- `sls`
- `archery`

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
  - 查线上日志、异常、调用链
- `archery-prod-query`
  - 查生产只读业务数据、状态字段、权限边界
- 本地代码定位
  - 在初始化配置的 `WORK_ROOT` 下按具体项目目录用 `rg` 缩小搜索范围

## 路由规则

### SLS 路由

先判断要查哪个 SLS project：

- 海外所有服务
  - `k8s-log-cd49222ac975346ad967c81f92a4a1c82 / datalogs`
  - 命令形态：`sls query --scope overseas --service <service> ...`
- 国内 `ai-open-api`
  - `94ai-all / 94ai-all`
  - 命令形态：`sls query --service ai-open-api ...`
- 国内其他服务
  - `k8s-log-c4128cc88f92f4757a78daa9f542b2446 / datalogs`
  - 命令形态：`sls query --scope domestic --service <service> ...`

如果用户只给 trace ID、号码、报错片段，仍不足以判断 project，必须先追问，不要盲切。

### Archery 实例路由

先判断要查哪个 Archery 实例：

- 国内大部分传统业务
  - `main-db`
  - 命令形态：`archery list-databases --scope domestic`
- 国内新业务
  - `polarDB-TiDB`
  - 覆盖：短信、短链、WA、回调、AgentCC
  - 命令形态：`archery list-databases --module sms|short-link|wa|callback|agentcc`
- 海外业务
  - `海外生产数据库`
  - 命令形态：`archery list-databases --scope overseas`

如果业务归属不清楚，先用 `archery list-databases --instance <instance>` 或 `archery list-tables ...` 缩小范围，不直接拍脑袋写 SQL。

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
