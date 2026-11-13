# Agent Toolkit

个人 AI Agent 工具箱，在多台电脑和多个 Agent 环境之间复用同一套能力与工作流。

包含 Codex 与 Claude Code 共用的 Skills、Codex / OpenCode / Claude Code custom Agents、全局协作规则、Codex Rules、OpenCode 配置、Playwright MCP 配置，以及跨平台安装脚本。工作专属能力通过安装分类隔离。

## 快速开始

### 选择安装分类

分类由 [`install-profiles.json`](install-profiles.json) 统一维护：

| 安装选择 | 使用场景 | 实际范围 |
|---|---|---|
| `--profile general` | “安装通用的”“家庭电脑安装” | 通用与个人用途组件 |
| `--profile work` | “安装工作用的”“工作新机安装” | **通用组件 + 工作扩展** |

首次安装必须显式选择；成功后记录在 `~/.local/agent-toolkit/install-profile.json`，同一用户的不同检出共用。新记录不存在时兼容读取本检出的 `.local/install-profile.json`，成功安装后迁移到新位置并保留旧记录。dry-run 不保存选择，切换分类不自动卸载已有组件。

### macOS / Linux

先克隆仓库并还原锁定的外部 UI/UX Skill；安装器不会隐式联网下载：

```bash
git clone git@github.com:konghanghang/agent-toolkit.git ~/.agent-toolkit
cd ~/.agent-toolkit
python3 scripts/update_ui_ux_pro_max.py --from-lock
```

家庭电脑或通用安装：

```bash
python3 scripts/install.py --profile general --dry-run
python3 scripts/install.py --profile general
```

工作电脑改用以下步骤，并初始化 工作公共运行时；通用安装无需 bootstrap：

```bash
python3 scripts/install.py --profile work --dry-run
python3 scripts/install.py --profile work
python3 common/bootstrap_work.py
```

工作区和服务凭证在本机按需配置，不进入 Git。安装参数、自定义 Codex Home、配置保护与运行时说明见 [安装指南](docs/installation.md)。

SLS 路由、TAPD 周报工作区和成员通过 [工作环境私有配置](docs/installation.md#工作环境私有配置)管理，仓库只提供空模板。

### Windows

使用 Python 3.11、Windows PowerShell 和本机 NTFS 卷，命令以 `py -3.11` 执行。完整的依赖检查、安装和更新步骤见 [Windows 指南](docs/windows.md)。

## 能力索引

以下按安装分类维护一份 Skills 清单；点击名称查看使用契约。`work` 安装包含下面两组。

### 通用 Skills（general）

| Skill | 用途 |
|---|---|
| [feature-delivery](skills/feature-delivery/SKILL.md) | 功能、修复、重构、脚本、Skill、配置与文档改动的通用开发流程 |
| [git-commit](skills/git-commit/SKILL.md) | 检查、暂存、提交和按授权推送 Git 变更 |
| [grilling](skills/grilling/SKILL.md) | 问透未定想法，澄清行为、范围及兼容性 |
| [domain-modeling](skills/domain-modeling/SKILL.md) | 记录稳定业务术语与必要架构决策 |
| [local-browser-verify](skills/local-browser-verify/SKILL.md) | 使用 Playwright MCP 验证网页实际行为 |
| [ui-ux-pro-max](docs/maintenance.md#外部-skill-更新) | UI/UX 设计与实现；外部 Skill，需按锁文件还原 |
| [capture-iminling-draft](skills/capture-iminling-draft/SKILL.md) | 将对话知识整理为个人 iminling 博客草稿 |
| [upload-to-easyimage](skills/upload-to-easyimage/SKILL.md) | 上传本地图片到 EasyImage 并验证远程结果 |

### 工作扩展 Skills（work）

| Skill | 用途 |
|---|---|
| [work-feature-delivery](skills/work-feature-delivery/SKILL.md) | 复用通用开发流程，叠加工作项目的需求记录、分支、提测与上线要求 |
| [work-url-permission-sql](skills/work-url-permission-sql/SKILL.md) | 为新增接口生成 URL 权限登记及核对 SQL，不执行数据库写入 |
| [online-issue-triage](skills/online-issue-triage/SKILL.md) | 串联日志、代码与数据证据排查线上和测试环境问题 |
| [aliyun-sls-log-query](skills/aliyun-sls-log-query/SKILL.md) | 查询阿里云 SLS 生产日志 |
| [jumpserver-k8s-log-query](skills/jumpserver-k8s-log-query/SKILL.md) | 经 JumpServer 查询测试环境 K8S 日志 |
| [archery-prod-query](skills/archery-prod-query/SKILL.md) | 通过 Archery 只读查询生产 MySQL 与 ClickHouse |
| [prometheus-query](skills/prometheus-query/SKILL.md) | 查询 Prometheus / VictoriaMetrics 监控指标 |
| [nacos-config-query](skills/nacos-config-query/SKILL.md) | 只读查询国内、海外 Nacos 配置及翻译 |
| [verify-test-db](skills/verify-test-db/SKILL.md) | 查询、验证测试库数据，按确认计划受控写入 |
| [tapd-story](skills/tapd-story/SKILL.md) | 读取需求、整理实施上下文，按授权创建或更新需求 |
| [tapd-weekly-report](skills/tapd-weekly-report/SKILL.md) | 从 TAPD 需求生成成员工作周报 |
| [gitlab-run-pipeline](skills/gitlab-run-pipeline/SKILL.md) | 按计划和授权触发、跟踪 GitLab Pipeline |

### Agents、Rules 与平台配置

Codex、OpenCode 与 Claude Code 配套维护五类通用 Agent：`evidence_explorer`、`reviewer`、`implementer`、`verifier` 和 `design_challenger`。职责与权限差异见 [维护指南](docs/maintenance.md#通用-agents)。

通用组包含这些 Agents、OpenCode 配置、Codex Playwright MCP 配置、Claude Code 通用设置片段（只读 Git 权限、拒绝规则）与 ccline 状态栏配置（二进制需先用 npm 安装，见 [安装结果](docs/installation.md#安装结果与配置保护)）、通用协作规则 `instructions/general.md`，以及 `git-commit.rules`、`github-readonly.rules`。工作组额外包含 `work-readonly.rules`、`gitlab-pipeline.rules`、Claude Code 工作设置片段，安装到 `--codex-home` 指定的工作专用 Codex Home 时在通用协作规则后追加 `instructions/work.md`，并按所选 Skills 创建私有配置模板。协作规则生成到 Codex Home 的 `AGENTS.md` 和 `~/.claude/CLAUDE.md`，默认 Home 只含通用规则，见 [安装结果](docs/installation.md#安装结果与配置保护)。平台和组件仍可通过安装参数筛选；Claude-only 不写 Codex 配置。

OpenCode 的 Gemini 使用 Google 原生 provider，通过 NewAPI 的 `/v1beta` 接入；本机变量、模型档位与服务重启步骤见 [OpenCode 使用说明](docs/opencode.md)。

Rules 的允许规则不替代用户授权或 Skill 的操作边界。具体范围见 [Rules 说明](docs/maintenance.md#codex-rules)，安装路径与配置保护见 [安装结果](docs/installation.md#安装结果与配置保护)。

### 开发工作流

通用开发从 `feature-delivery` 进入，工作项目开发从 `work-feature-delivery` 进入并复用通用主线。根据任务目标、项目规则和已有授权，从当前阶段继续。

完整流程图、专项 Skill 配合与 Agent 分工见 [开发工作流](docs/workflows.md)。

## 日常更新

拉取仓库后，先预览，再按本机已记录的 profile 更新安装：

```bash
git -C ~/.agent-toolkit pull
python3 ~/.agent-toolkit/scripts/install.py --dry-run
python3 ~/.agent-toolkit/scripts/install.py
```

目录链接中的 Skill 和 Agent 源文件随仓库更新；Rules、协作规则 `AGENTS.md`、受管 Playwright 配置和 Windows 的 OpenCode 配置副本需重新安装。完成后新开 Agent 会话或重启客户端；OpenCode 配置变更后执行 `opencode reload` 或重启服务。环境变量变更需按 [OpenCode 使用说明](docs/opencode.md#本机环境变量)加载私有配置、同步服务地址并处理旧界面的自动启动，重启成功本身不能证明新环境已生效。

外部 UI/UX Skill 的更新与锁文件还原见 [维护指南](docs/maintenance.md#外部-skill-更新)。

## 详细文档

| 文档 | 查阅内容 |
|---|---|
| [安装指南](docs/installation.md) | 安装范围与参数、迁移、自定义 Home、配置保护、工作运行时与可选依赖 |
| [Windows 指南](docs/windows.md) | 前置条件、PowerShell 安装、junction、命令入口与 ExecutionPolicy |
| [OpenCode 使用说明](docs/opencode.md) | NewAPI 原生 Gemini、环境变量、模型档位、服务重启与流式工具调用排查 |
| [开发工作流](docs/workflows.md) | 通用与工作 交付流程图、专项 Skill 配合、Agent 分工 |
| [维护指南](docs/maintenance.md) | 目录职责、兼容契约、新增组件、更新机制与验证清单 |
| [工作流行为评估](evals/workflow/README.md) | 离线样例准备、真实模型任务执行与证据边界 |

维护仓库前阅读 [AGENTS.md](AGENTS.md)，提交前按 [验证与提交检查](docs/maintenance.md#验证与提交检查)核对影响范围。

## 许可证

本项目采用 [MIT License](LICENSE)。外部 Skills 及第三方组件遵循各自的许可证。
