# Agent Toolkit 项目约定

## 安装分类

- 分类唯一来源为 `install-profiles.json`。每个新增 Skill（含外部 Skill）必须显式登记 `general` 或 `work`，同时更新 README 分类说明和相关测试；不得依赖名称前缀猜测或默认归类。
- `general`：个人开发、设计、博客等通用与个人用途，不依赖 工作运行时或工作凭证。
- `work`：工作专属服务、凭证和流程扩展。安装 profile `work` 包含 general + work；用户说“安装工作用的”无需再要求其指定通用组。
- “安装通用的”“家庭电脑安装”使用 `--profile general`；“安装工作用的”“工作新机安装”使用 `--profile work`。没有明确用途时先查 `~/.local/agent-toolkit/install-profile.json`；新位置不存在时兼容读取本检出的 `.local/install-profile.json`。首次未选择时询问用途，不默认安装全部；成功安装只写机器级新位置，旧记录保留。
- 新 Rule 同样必须分类。当前 custom Agent 目录、OpenCode 配置和 Playwright 配置均是通用组件；增加工作专属 Agent/MCP 前必须先实现隔离，不能混入通用共享目录或模板。
- 安装前运行对应 profile 的 dry-run。`--skill` 不能越过 profile 边界。切换分类不自动卸载；需要卸载时单独确认范围。
- 新机完整安装先通过 `scripts/update_ui_ux_pro_max.py --from-lock` 还原外部 Skill，再按 profile 安装。工作新机还需执行 `common/bootstrap_work.py` 初始化运行时；家庭电脑不运行它。
- 本机 profile、运行时、凭证和备份不进入版本控制。
- 工作环境的服务地址、实例与云资源标识、成员名单和工作区 ID 放入本机私有配置，不作为源码默认值或写进测试与公共示例。公共文档使用明确的虚构名称、保留示例域名及示例网段；实际项目结构由项目规则提供。
- `codex/agents/*.toml` 与 `opencode/agents/*.md` 必须按同名成对维护并保持功能一致。新增、修改或删除任意一边时，必须在同一次改动中同步另一边，不能只留待办。两边的职责、任务边界、证据要求、输出契约和完成任务所需的读取、搜索、Git 检查能力保持一致；模型、工具、权限和沙箱按各自平台表达，不要求配置字段相同。不得只核对文件名或正文，也不得让一边独有的权限或步数限制削弱功能；无法等价的隔离机制须明确记录。当前成对 agent 是 `evidence_explorer`、`reviewer`、`implementer`、`verifier` 和 `design_challenger`。

## 验证

修改安装逻辑需覆盖 general 不安装工作 Skills/Rules/模板、work 包含通用组、首次选择、本机记录、dry-run 不写入和未知分类拒绝等回归测试，并分别检查两个 profile 的安装预览。保留 Windows 支持和已有配置保护。

Agent 改动需核对两端名称集合和功能契约，并按职责验证能力：探索、审查和设计质疑保持只读；实现只修改交接范围；验证允许必要运行产物但不修改受审实现或正式测试。所有角色的读取、搜索和 Git 检查应可用，不自行派生或执行 Git 交付。Codex 自定义角色继承父会话权限，不能用角色文件中的 `sandbox_mode` 声称独立沙箱；OpenCode 子角色权限不是父角色权限的子集；直接工具限制也不能证明允许的 shell 不会间接写入。配置、原生加载、模型行为和系统隔离分别验证，不互相替代。安装逻辑变更先核对各平台的链接权限、路径表示、配置保护和重复安装行为，再选择回归验证。提交前按 [维护指南的影响范围清单](docs/maintenance.md#验证与提交检查)执行检查；复用结果时注明对应版本与覆盖范围，未执行、跳过和真实环境验收分别说明。
