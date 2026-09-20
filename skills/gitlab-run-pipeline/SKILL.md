---
name: gitlab-run-pipeline
description: "通过自建 GitLab API 触发、编排和监控 Pipeline。用于单项目 CI、按依赖顺序批量构建、构建指定分支最新提交及查询最终结果；不自动取消、重试失败流水线或执行 manual job。"
---

# GitLab Pipeline 触发与构建编排

统一使用 `gitlab` 短命令。默认在每个项目实际触发前读取已授权 ref 的最新提交；批量构建按产物依赖放行，不把所有项目串行等待到整条流水线结束。

## 模块触发策略

- **同一项目多个模块**：仅发布部分模块时默认逐模块触发，前一个模块完成实际构建、镜像及部署链路后再触发下一个，用于降低共享 Runner、Maven 缓存和 Docker 构建资源竞争；用户明确要求并行时按授权执行。
- **全量模块**：仅当授权范围覆盖该项目全部模块，且已核实 CI 支持 `SUB_MODULE=all` 的真实含义时，优先使用一次全量流水线；不能因方便而扩大发布范围。
- **不同项目**：按真实产物依赖调度。无依赖项目可以并行；上游所需产物发布成功即可放行下游，同时继续监控上游部署，不套用同项目模块串行规则。
- 资源竞争导致的执行串行不等于业务或产物依赖。需要限制跨项目并发时，依据已确认的资源约束说明调度方式，保留真实依赖关系。
- 产物阶段成功不表示服务已发布。每条已触发流水线按实际 CI 任务和阶段持续监控到终态；不假定所有项目都有 `build_images` 或 `deploy`。失败、manual、取消或超时按实际结果报告，不等待到永远成功。
- Pipeline 成功仅证明该流水线实际包含的链路完成；未包含部署时不能报告已发布，包含部署时仍不等同于 Pod 健康或业务接口验收。运行态验收另行检查 Pod/Ready、健康接口和日志。

涉及多个项目时，读取 [references/multi-project-builds.md](references/multi-project-builds.md)，按其中的阶段放行和并行规则执行。

## 本机配置

首次使用先初始化公共运行时：

```bash
python3 "$AGENT_TOOLKIT_ROOT/common/bootstrap_work.py"
```

初始化在 `~/.local/agent-toolkit/work/runtime.json` 中记录 `gitlab_dir`，仅在文件不存在时创建私有空模板 `GITLAB_DIR/gitlab-credentials.json`：

```json
{
  "base_url": "",
  "private_token": ""
}
```

用户填写两个 value。域名与 Token 只从私有文件读取，不写进命令参数、聊天、仓库或其他持久化文件。初始化器及读取脚本会收紧目录和文件权限：macOS/Linux 使用 `0700`/`0600`，Windows 使用受保护 ACL。

验证配置：

```bash
gitlab whoami
```

Windows PowerShell 使用公共入口：

```powershell
$AgentToolkitRoot = (Resolve-Path (Join-Path $HOME ".agent-toolkit")).Path
& "$AgentToolkitRoot\bin\gitlab.cmd" whoami
```

## 授权与范围

- `trigger` 是命令中的唯一写操作。只有查看、分析意图时不触发；用户已明确要求构建，且项目、ref、环境、模块及变量能从当前任务确定时，说明执行范围后继续，不重复索要同一授权。只询问真正缺失或超出原授权的内容。
- 默认“构建指定分支”表示触发时取该分支最新提交；授权绑定项目、ref、环境、模块和变量范围，不要求用户逐个确认 SHA 或 `plan_id`。用户明确指定固定提交/固定批次时保留固定模式，不能自动更新目标 SHA。
- `--env` 必须显式传入；无环境概念的 CI 使用 `--no-environment`。环境优先取用户当前或此前明确口径，不采用 YAML 默认值替代用户选择。`prod`、`production`、`grayscale` 必须由用户明确点名，并传 `--allow-prod`。
- 若既有流水线会部署，先说明实际副作用；用户只授权构建、不包含部署时，不直接触发包含部署的流水线。配置和 SQL 前置要求按本次真实交付范围核对，不把构建授权当作数据库写入授权。
- 从当前仓库解析时，Git remote host 必须与私有配置的 GitLab host 一致。规范项目已由当前任务的 remote 或先前核验确定时直接复用；只有名称仍模糊时才 `gitlab search --query <name>`，不要猜相似项目。
- `--var` 仅允许非敏感变量，禁止 `CI_*` 和重复变量。不覆盖 `CI_PIPELINE_SOURCE` 伪装网页触发。
- 网页 Run pipeline 的来源是 `web`，Create Pipeline API 的来源是 `api`。API 被 CI 规则拒绝不代表网页构建也不能运行；报告真实错误，修改 CI 或改用网页登录方式须在相应授权范围内执行。
- 查询和监控只读。不自动取消、重试已创建的失败流水线、执行 manual job、下载 artifact 或读取完整 job trace；不管理 MR、Issue、Release、Runner。

## 临触发前准备计划

当前目录就是目标仓库时可先解析：

```bash
gitlab resolve
```

**在该项目满足触发条件时再 inspect**，不要在整批开始时固定全部 SHA：

```bash
gitlab inspect --project group/project --ref test --env test --module core
```

记录规范路径、Project ID、URL、ref、当时 SHA、CI 配置摘要、环境、模块、变量及 `trigger_plan.plan_id`。单次计划仍绑定这些值，`trigger` 原样携带该计划 ID 和 `--confirm`；`--confirm` 表示已有用户授权，不等于每次必须重新提问。

- 最新提交模式：inspect 后立即 trigger。若计划检查因 ref 推进而拒绝、且尚未创建流水线，可以重新 inspect；确认项目、ref、环境、模块、变量及流水线实际行为仍在授权范围内后，使用新计划继续，不因 SHA 正常前进反复询问用户。CI 摘要变化要检查是否新增部署、环境等范围变化，不能盲目接受。
- 计划重取最多 2 次；持续变化则报告该项目尚未创建，避免无界追逐。网络超时等导致 POST 是否创建不明确时，先只读核对最新 Pipeline 的 SHA、时间和触发用户，不能直接重复创建。
- 固定提交模式：trigger 加 `--expected-sha '<已确认的完整 SHA>'`，创建前后都核对；预检不匹配时不会创建。Create Pipeline API 按 ref 创建，工具无法原子锁定可变分支；严格固定须使用已核验且不会移动的 ref，没有这种 ref 时先说明无法保证严格固定，不直接触发可变分支，也不自行创建 tag。不能套用最新模式替换目标。
- 创建后返回 `commit_mismatch`（退出码 2）表示流水线已经创建，但实际 SHA 不符或缺失。保留返回的 ID、URL 和实际 SHA，停止该项目的下游放行，继续按 ID 只读查询并报告偏差；不因错误退出码重复创建或自动取消。
- 创建成功后以 GitLab 返回的 Pipeline ID 和实际 SHA 为准。ref 可能在预检与创建间前进，最新模式记录实际 SHA；不为追赶之后出现的提交重复触发，也不回头检查或重建已经放行的上游。
- CI lint 可用时校验准确 ref 的服务端配置及 include；不可用时如实报告。Create Pipeline API 才是 API 触发是否可运行的最终依据，不用正则检查冒充服务端执行结果。

## 单项目触发与监控

```bash
gitlab trigger --project group/project --ref test --env test --module core \
  --plan-id '<trigger_plan.plan_id>' --confirm
```

单项目默认等待最终结果。用户只要触发时可用 `--no-wait`；批量编排也使用 `--no-wait`，由 Agent 轮询阶段和最终状态，并不表示放弃等待整批结果。

监控默认每 15 秒查询一次、最多 30 分钟，只在状态变化或 60 秒 heartbeat 时报告进度：

- `success`、`failed`、`canceled`、`skipped`：记录该流水线最终结果；批量任务继续处理不受其影响的项目。
- `manual`、`blocked`：报告人工操作项，不自动执行。它是否阻塞下游取决于产物阶段是否已成功发布，见批量规则。
- `created`、`waiting_for_resource`、`preparing`、`pending`、`running`、`scheduled`：继续等待。
- 只读查询遇到网络错误、429 或 5xx 最多连续重试 3 次；401、403、404 停止该项目查询并报告。超时只停止监控，不取消 Pipeline，也不把未知状态当成功。

## 查询结果

已触发的流水线始终按记录的 ID 查询，避免误跟踪别人后来触发的构建：

```bash
gitlab result --project group/project --pipeline-id 12345
```

不知道 ID 时才按 ref 选择最新一次，并核对 SHA、source、创建时间和用户：

```bash
gitlab result --project group/project --ref test
```

单项目等待终态可加 `--wait`。批量运行时用非阻塞轮询；结果中的 `stage_summary` 汇总普通 job 与 bridge，提供任务数、状态计数及 `all_success`。bridge 还要求关联下游流水线状态为 success；任一任务列表查询失败或截断都不能据此放行。阶段成功不替代产物已发布的 CI 契约核验。

最终汇报项目、ref、环境、模块、实际 SHA、Pipeline ID/URL、状态、耗时和 job 计数。批量任务区分“依赖产物阶段通过”和“整条流水线最终成功”；未触发项目要列明阻塞依赖。失败列出 job 名称、stage、`failure_reason` 和 URL，`allow_failure` 的失败单列；说明 manual、超时或查询失败的未完成项。流水线成功不等于完整业务验收成功。
