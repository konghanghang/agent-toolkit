---
name: gitlab-run-pipeline
description: "通过自建 GitLab API 触发 Pipeline，并持续监控或查询最终结果。用于跑 CI、打指定环境、Run pipeline、等待流水线结束、查看刚才或指定 Pipeline 是否成功等场景；不管理 MR、Issue、Release、Runner，也不自动取消、重试或执行 manual job。"
---

# GitLab Pipeline 触发与结果查询

围绕两个用户意图工作：触发 Pipeline，以及查询最终结果。统一使用 `gitlab` 短命令；不要手写 `curl`，不要把域名、Token 或其他秘密写进 Skill、命令参数、聊天记录或仓库文件。

## 本机配置

首次使用先初始化公共运行时：

```bash
python3 "$AGENT_TOOLKIT_ROOT/common/bootstrap_work.py"
```

初始化会在 `~/.local/agent-toolkit/work/runtime.json` 中保存 `gitlab_dir`，并在文件不存在时创建私有空模板 `GITLAB_DIR/gitlab-credentials.json`。已有文件绝不覆盖；用户只需填写两个 value：

```json
{
  "base_url": "",
  "private_token": ""
}
```

把空字符串分别替换为实际 GitLab 地址和 Token，不改字段名。`base_url` 和 `private_token` 只从这个私有文件读取。初始化器及读取脚本都会收紧目录和文件权限：macOS/Linux 使用 `0700`/`0600`，Windows 使用受保护 ACL。验证配置：

```bash
gitlab whoami
```

Windows PowerShell 使用初始化生成的公共入口：

```powershell
$AgentToolkitRoot = (Resolve-Path (Join-Path $HOME ".agent-toolkit")).Path
& "$AgentToolkitRoot\bin\gitlab.cmd" whoami
```

## 安全边界

- `trigger` 是唯一写操作。用户说“看一下”“能不能触发”不等于授权；创建前必须展示项目、ref、环境、模块和额外变量，并获得明确确认。
- `--env` 必须显式传入；没有环境概念的普通 CI 必须显式使用 `--no-environment`。不得采用 YAML 默认环境代替用户选择。
- `prod`、`production`、`grayscale` 必须由用户明确点名，并同时传 `--allow-prod`。
- 自动解析当前仓库时，Git remote host 必须与本机 GitLab `base_url` 一致；不一致时停止。显式目标使用 `--project namespace/project`。
- 用户只给简称、中文名或其他模糊名称时不得猜 `namespace/project`；先使用 `gitlab search --query <name>`，没有结果或多个候选时让用户选择。即使只有一个精确候选，也要展示规范路径和 URL 后再准备触发。
- `inspect` 生成的确认计划必须绑定 GitLab `project_id`、规范项目路径、ref、commit SHA、服务端 CI 配置摘要、环境、模块和全部非敏感变量。`trigger` 必须同时携带用户看过的 `plan_id` 和 `--confirm`；任一项变化都停止并重新确认。
- GitLab CI lint 可用时，用它校验准确 ref 的服务端配置和 include；Create Pipeline API 才是 `source=api` 是否实际可创建的权威结果，不使用文本正则冒充确定性判断。
- `--var` 只允许非敏感变量，禁止 `CI_*` 和重复变量。秘密应预先配置为 GitLab CI/CD 变量，不得通过命令行传递。
- 监控和结果查询都是只读操作。超时只停止监控，不取消 Pipeline。
- 不自动取消、重试、执行 manual job、下载 artifact 或读取完整 job trace；这些能力不属于当前 Skill。

## 触发并等待结果

当前目录就是目标仓库时，优先使用 Git origin，并先确认解析结果：

```bash
gitlab resolve
```

用户只给了模糊名称时先只读搜索：

```bash
gitlab search --query company
```

不得自动选择相似度最高的项目。让用户从候选中的 `path_with_namespace` 和 `web_url` 选择，然后使用准确路径准备触发计划：

```bash
gitlab inspect \
  --project group/project \
  --ref test \
  --env test \
  --module all
```

向用户展示规范 `project`、Project ID、Web URL、ref、commit SHA、CI 配置摘要、`ENVIRONMENT_NAME`、`SUB_MODULE`、其他变量和 `plan_id`。用户确认这一整组值后，原样传入 `trigger`；默认持续监控到最终状态：

```bash
gitlab trigger \
  --project group/project \
  --ref test \
  --env test \
  --module all \
  --plan-id '<trigger_plan.plan_id>' \
  --confirm
```

`trigger` 会重新读取项目、ref commit 和 CI 配置，再计算一次计划。`plan_id` 不一致时不得继续，也不得自行用新 ID 替换后触发；必须向用户展示变化并重新确认。用户明确只要触发、不等待时才加 `--no-wait`。生产或灰度还需 `--allow-prod`。

监控默认每 15 秒查询一次、最多 30 分钟，只在状态变化或 60 秒 heartbeat 时报告进度：

- `success`、`failed`、`canceled`、`skipped`：形成最终结果并停止。
- `manual`、`blocked`：报告需要人工操作并停止；不得自动执行 manual job。
- `created`、`waiting_for_resource`、`preparing`、`pending`、`running`、`scheduled`：继续等待。
- 网络错误、429 或 5xx 最多连续重试 3 次；401、403、404 立即停止。

## 查询执行结果

知道 Pipeline ID 时：

```bash
gitlab result --project group/project --pipeline-id 12345
```

不知道 ID 时按项目和 ref 选择最新一次，并在结果中核对 ID、SHA、source、创建时间和触发用户：

```bash
gitlab result --project group/project --ref test
```

如果仍在运行，用户要求等最终结果时加 `--wait`。不得把其他 ref、其他提交或其他用户触发的 Pipeline 当成“刚才那一次”。

最终结果应包含：

- 项目、ref、环境、模块；
- Pipeline ID、状态、SHA、耗时和 Web URL；
- job 总数及状态计数；
- 非允许失败的 job 名称、stage、`failure_reason` 和 Web URL；
- `allow_failure` 的失败单独列出；
- manual job 或监控超时的未完成原因。

失败时先提供 job URL 和结构化失败摘要。只有用户另行要求分析失败日志时，才讨论增加受限、脱敏的 trace 查询；当前 Skill 不读取完整构建日志。
