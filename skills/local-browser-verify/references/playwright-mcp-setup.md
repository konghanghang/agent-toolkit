# Playwright MCP 前置检查与安装

只在当前 Agent 会话缺少 Playwright MCP 浏览器能力，或 MCP 已配置但无法调用时读取本文件。不要假定宿主是 Codex，也不要根据模型名称猜客户端、配置格式或安装命令。

## 判断顺序

1. 检查当前会话实际暴露的工具，确认是否能导航、读取页面、交互和执行页面求值。按能力判断，不要求固定的工具名称或前缀。
2. 识别当前 Agent 宿主、客户端形态和版本，例如 Codex、Claude Code、OpenCode、Kimi 或 Grok 的 CLI、IDE 插件或桌面端。
3. 使用该宿主的只读 MCP 状态命令或设置界面，判断 Playwright MCP 是“未配置”“已禁用”“启动失败”还是“已经配置但当前会话未加载”。
4. 已配置但当前会话不可用时不要重复安装。先检查启用状态、启动错误和 Node.js / `npx`，再按宿主要求新开会话或重启客户端。
5. 尚未配置时，说明将修改哪个宿主、用户级还是项目级配置，以及 `npx` 首次启动可能联网下载 npm 包；等待用户明确同意后才能执行。

配置存在不等于当前会话已加载。只有实际出现并能调用 Playwright MCP 浏览器能力，前置条件才算通过。不支持 MCP 的宿主直接返回 `BLOCKED`。

## 通用 MCP Server

Playwright MCP 的 STDIO Server 启动命令是：

```bash
npx -y @playwright/mcp@latest \
  --user-data-dir "$HOME/.local/share/playwright-profile"
```

这只是 MCP Server 命令，不是所有 Agent 通用的安装命令。不同宿主会通过 CLI 参数、JSON、TOML、IDE 设置或桌面端界面包装它。执行前必须从当前客户端的内置帮助或官方文档核对准确配置方式；不能把 Codex 命令套给 Claude Code、OpenCode、Kimi 或 Grok，也不能凭记忆编造命令。

本机约定统一使用 `~/.local/share/playwright-profile` 持久化登录态。宿主直接传递 MCP 参数而不经过 shell 时，配置中必须使用该路径解析后的绝对值，不能写字面量 `~`；不要同时使用 `--isolated`。该 profile 可被不同 Codex Home 依次复用，但不要让多个浏览器实例并发占用。

如果团队要求可复现版本，先让用户确认具体版本，再将 `@latest` 替换为该版本；不要擅自固定或升级。

## Codex 示例

仅当当前宿主确实是 Codex 时使用本节。

先只读检查：

```bash
node --version
npx --version
codex mcp list
```

用户明确同意后配置：

```bash
codex mcp add playwright -- npx -y @playwright/mcp@latest \
  --user-data-dir "$HOME/.local/share/playwright-profile"
```

配置后再次运行 `codex mcp list`，新开 Codex 会话或重启客户端，并在 TUI 中用 `/mcp` 确认 Playwright 已启用。

## 其他 Agent

Claude Code、OpenCode、Kimi、Grok 或其他宿主按同一边界处理：

1. 从当前客户端的 `--help`、MCP 设置页或官方文档确认配置入口。
2. 向用户展示将执行的准确命令或配置 diff、写入范围和可能的下载行为。
3. 用户明确同意后才执行。
4. 重新加载宿主要求的新会话或客户端，再检查实际浏览器工具。

无法核实当前宿主的准确安装方式时，给出通用 MCP Server 命令和待填写的配置字段即可，明确要求用户参考该宿主官方文档；不要用另一个 Agent 的命令碰运气。

## 权限边界

- Skill 被触发、用户要求验收或 Playwright MCP 缺失，都不等于允许安装。
- 未经授权不得修改任何 Agent 的用户级或项目级 MCP 配置，不得安装 Node.js、下载浏览器或调整工具审批策略。
- 用户拒绝安装时，说明无法完成浏览器验收并返回 `BLOCKED`；不要把代码审查、HTTP 探活或截图冒充浏览器验收。
- MCP 已可用时，不再要求某个特定 Agent、配置文件或安装方式。

参考来源：Playwright MCP 的上游项目为 [microsoft/playwright-mcp](https://github.com/microsoft/playwright-mcp)；Codex 的配置示例见 [OpenAI Codex MCP 文档](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)。
