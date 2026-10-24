# OpenCode 使用说明

[返回首页](../README.md) · [安装指南](installation.md#安装结果与配置保护) · [Windows 指南](windows.md) · [维护指南](maintenance.md)

本仓库维护 [OpenCode 配置](../opencode/opencode.json)和五类通用 Agents。Gemini 通过 NewAPI 的 Google 原生接口接入，provider ID 保持为 `gemini`。以下配置与排查说明以本机 OpenCode 2.1.4 的观察为依据；升级客户端或网关后，应重新验证实际使用的模型与工具回合。

## Provider 与模型

| Provider ID | Package | 地址变量 | 密钥变量 |
|---|---|---|---|
| `grok` | `@opencode/ai/providers/xai` | `NEWAPI_BASE_URL` | `GROK_API_KEY` |
| `gemini` | `@opencode/ai/providers/google` | `NEWAPI_GEMINI_BASE_URL` | `NEWAPI_GEMINI_API_KEY` |
| `codex` | `@opencode/ai/providers/openai-compatible` | `NEWAPI_BASE_URL` | `NEWAPI_CODEX_API_KEY` |
| `glm` | `@opencode/ai/providers/openai-compatible` | `NEWAPI_BASE_URL` | `NEWAPI_ZAI_API_KEY` |

`NEWAPI_BASE_URL` 使用网关的 `/v1` 路径，例如 `https://gateway.example.com/v1`。`NEWAPI_GEMINI_BASE_URL` 使用同一网关的 `/v1beta`，例如 `https://gateway.example.com/v1beta`；`/v1` 和 `/v1beta/openai` 都不适用于这里的 Google 原生 provider。确认 NewAPI 的 Gemini 通道和 Token 允许所选模型。

Gemini 模型 ID 为 `gemini/gemini-4-argon` 和 `gemini/gemini-3.8-flash`，各有 `low`、`medium`、`high` 三个 variant。完整引用例如 `gemini/gemini-4-argon#low`。这些档位在配置中使用 `settings.thinkingConfig.thinkingLevel`，取值对应 `low`、`medium`、`high`；不要沿用 OpenAI 兼容 provider 的 `reasoningEffort` 字段。

仓库默认模型仍为 `grok/grok-4.7`，配置 Gemini 不会自动切换当前 Agent 的模型。需要使用 Gemini 时，在模型选择中明确指定对应模型和档位；Agent 自身指定了模型时也应核对其配置。

## 本机环境变量

地址和 Token 保存在本机私有配置，仓库中只保留 `{env:NAME}` 引用。以下均为示例域名与占位 Token，使用前替换为本机配置。

### macOS / zsh

将以下内容放入本机私有文件，例如 `~/.config/opencode/env.zsh`；已有统一私有 Shell 文件时可继续使用原路径：

```zsh
export NEWAPI_BASE_URL='https://gateway.example.com/v1'
export NEWAPI_GEMINI_BASE_URL="${NEWAPI_BASE_URL%/v1}/v1beta"
export GROK_API_KEY='REPLACE_WITH_GROK_TOKEN'
export NEWAPI_GEMINI_API_KEY='REPLACE_WITH_GEMINI_TOKEN'
export NEWAPI_CODEX_API_KEY='REPLACE_WITH_CODEX_TOKEN'
export NEWAPI_ZAI_API_KEY='REPLACE_WITH_GLM_TOKEN'
```

上面的派生写法要求 `NEWAPI_BASE_URL` 以 `/v1` 结尾且没有末尾斜杠；也可以显式设置 `NEWAPI_GEMINI_BASE_URL='https://gateway.example.com/v1beta'`。

在 `~/.zshrc` 中加载该文件：

```zsh
[[ -r "$HOME/.config/opencode/env.zsh" ]] && source "$HOME/.config/opencode/env.zsh"
```

`export` 使变量进入 OpenCode 子进程的环境。zsh 的 `typeset -g NAME=...` 只将变量设为全局，不能替代导出；在终端能读取变量，也不能证明 OpenCode 能继承它。

先在当前终端加载全部已配置 provider 的私有地址和 Token，再将 Gemini 地址同步到本机服务配置并重启服务：

```zsh
source ~/.zshrc
opencode service set env NEWAPI_GEMINI_BASE_URL "$NEWAPI_GEMINI_BASE_URL"
opencode service restart
```

`service set env` 将这项地址覆盖写入本机 `~/.config/opencode/service.json` 的 `env`，文件不进入 Git。地址变更时重新执行该命令。Token 继续通过私有 Shell 的 `export` 提供；每次 `service restart` 都必须从已加载全部私有 Token 的终端执行，否则其他 provider 也可能因密钥缺失而不可用。取消地址覆盖使用 `opencode service unset env NEWAPI_GEMINI_BASE_URL`，随后也需按启动环境重新启动服务。

长期运行的旧 OpenCode 界面可能在后台服务停止或重启后立即自动拉起新服务，抢先沿用旧环境。同步上述地址可使自动拉起的服务也读取新增地址；重启成功本身仍不能证明全部变量生效。变更 Token 或其他进程变量时，先退出旧界面，再从已加载私有配置的新终端启动服务和界面，并核对实际请求。其他终端或桌面启动入口也需具备相同的环境。

已有服务进程不会继承当前终端后来发生的环境变化；仅执行 `opencode reload` 会重新加载配置，无法更新该进程的环境。

### Windows / PowerShell

在将要启动 OpenCode 的 PowerShell 中设置当前进程的变量。以下只演示 Gemini；重启前须按上表加载全部已配置 provider 的私有地址和 Token：

```powershell
$env:NEWAPI_GEMINI_BASE_URL = 'https://gateway.example.com/v1beta'
$env:NEWAPI_GEMINI_API_KEY = 'REPLACE_WITH_GEMINI_TOKEN'
opencode service set env NEWAPI_GEMINI_BASE_URL $env:NEWAPI_GEMINI_BASE_URL
opencode service restart
```

`$env:` 赋值只对当前 PowerShell 及其后续子进程生效；`service set env` 另将 Gemini 地址保存为本机服务覆盖。地址修改后重新同步，取消覆盖使用前述 `service unset env` 命令。变更 Token 或其他变量时同样先退出旧界面，再从已加载新环境的 PowerShell 启动。持久 Shell 配置保存在本机私有启动配置中。Windows 更新仓库配置后还需重新安装受管副本，命令见 [Windows 更新步骤](windows.md#更新)。

## 为什么 Gemini 使用原生接口

本机 OpenCode 2.1.4 的 OpenAI Chat 流式解析器在收到 `finish_reason` 后，会拒绝继续出现的正文、推理或带 ID、名称、参数的 `tool_calls` 增量，错误为 `OpenAI Chat received content after the finish reason`。一次回复连续发出多个工具调用时，即使部分工具调用已显示，也可能随后触发该错误。

已定位的 NewAPI `v1.0.0-rc.46` 在 Gemini → OpenAI 流式转换时，只要当前块包含工具调用，就设置 `finish_reason=tool_calls`；后续仍可能有工具块。代码见 [转换器赋值](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.46/relaykit/relayconvert/internal/gemini_chat/to_oai_chat_resp.go#L275-L277)。中继层只在转为 Claude 格式时清空工具块的结束标记，见 [Gemini 中继处理](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.46/relay/channel/gemini/relay-gemini.go#L318-L324)。这一顺序会触发上述严格解析检查。

Google 原生 provider 使用 Gemini 的请求与流式响应格式，绕开这段 Gemini → OpenAI 转换。它仍经过 NewAPI 网关；模型权限、通道配置、思考档位及后续版本的兼容性仍需按实际路径验证。

## 验证与排查

排查时先确认服务实际继承了变量及本机服务地址覆盖，再确认模型请求和完整工具回合。OpenCode 2.1.4 在本机观察到缺失的 `{env:NAME}` 会被替换为空字符串；配置成功加载或模型出现在列表中，都不能证明地址、Token 或模型调用有效。2026-10-24 的验证范围如下：

- **配置加载**：检查 Gemini 的 package、`/v1beta` 地址来源及两个模型的三档配置。隔离的 `opencode api --standalone` 配合 `GET /api/model` 已能加载这份配置；此检查不调用模型。
- **真实接口**：已确认 NewAPI `/v1beta/models` 返回两个 Gemini 模型；`gemini-4-argon` 的 `low` 档使用合成工具测试时连续返回三次工具调用，工具块不含 `finishReason`，最后独立返回 `STOP`。这只覆盖该模型、该档位与该测试请求。
- **完整工具回合**：OpenCode 2.1.4 使用 `gemini/gemini-4-argon#high`，在隔离目录、独立配置和数据下完成三次 `read`，分别读取 alpha、beta、gamma 测试文件；三次工具状态均为 `completed`，结果回传后模型准确回复 `VALIDATED alpha=11 beta=22 gamma=33`。测试将最大输出限制为 4096，只覆盖此模型、此档位和小型只读场景，未覆盖真实项目写入或全部模型档位。验收应包含工具执行、结果回传与最终答复，不能只看工具块或进程退出码。

临时检查前先确认命令的服务行为：`opencode debug config` 可能自动拉起受管服务；仅更换 `XDG_DATA_HOME` 不能隔离固定服务端口。需要独立检查时，使用支持 `--standalone` 的命令和独立配置，结束后确认临时进程已退出、没有残留服务。诊断输出可能包含解析后的环境值，不要把原始地址、Token 或配置快照提交到仓库。
