# Agent Toolkit 项目约定

## 安装分类

- 分类唯一来源为 `install-profiles.json`。每个新增 Skill（含外部 Skill）必须显式登记 `general` 或 `work`，同时更新 README 分类说明和相关测试；不得依赖名称前缀猜测或默认归类。
- `general`：个人开发、设计、博客等通用与个人用途，不依赖 work 运行时或工作凭证。
- `work`：工作专属服务、凭证和流程扩展。安装 profile `work` 包含 general + work；用户说“安装工作用的”无需再要求其指定通用组。
- “安装通用的”“家庭电脑安装”使用 `--profile general`；“安装工作用的”“工作新机安装”使用 `--profile work`。没有明确用途时先查本检出的 `.local/install-profile.json`；首次未选择时询问用途，不默认安装全部。
- 新 Rule 同样必须分类。当前 custom Agent 目录、OpenCode 配置和 Playwright 配置均是通用组件；增加工作专属 Agent/MCP 前必须先实现隔离，不能混入通用共享目录或模板。
- 安装前运行对应 profile 的 dry-run。`--skill` 不能越过 profile 边界。切换分类不自动卸载；需要卸载时单独确认范围。
- 新机完整安装先通过 `scripts/update_ui_ux_pro_max.py --from-lock` 还原外部 Skill，再按 profile 安装。工作新机还需执行 `common/bootstrap_work.py` 初始化运行时；家庭电脑不运行它。
- 本机 profile、运行时、凭证和备份不进入版本控制。
- `codex/agents/*.toml` 与 `opencode/agents/*.md` 必须按同名成对维护。新增、修改或删除任意一边时，必须在同一次改动中同步另一边，不能只留待办。两边保持同一职责和中文指令；模型、权限和沙箱按各自平台字段表达，不复制平台语法。提交前核对两个目录的 agent 名称集合一致。当前成对 agent 是 `evidence_explorer` 和 `reviewer`。

## 验证

修改安装逻辑需覆盖 general 不安装工作 Skills/Rules/模板、work 包含通用组、首次选择、本机记录、dry-run 不写入和未知分类拒绝等回归测试，并分别检查两个 profile 的安装预览。保留 Windows 支持和已有配置保护。
