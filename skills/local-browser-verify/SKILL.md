---
name: local-browser-verify
description: 在任意支持 MCP 的 Agent 中使用 Playwright MCP，对本机或用户明确指定的网页做通用验收，覆盖运行时 MCP 检查、URL 探活、登录会话、真实用户路径、响应式布局、异步状态、表单和媒体取证，以及调试产物隔离。适用于“本地验收”“打开页面验证”“检查窄屏或横向滚动”“验证表单、自动播放或下一条”“Playwright 怎么登录”或清理 `.playwright-mcp` 等场景。Playwright MCP 缺失时按当前 Agent 的配置方式提示安装并等待授权，不自行安装，也不临时改用 CDP、Puppeteer 或浏览器驱动脚本。不替代项目 QA Skill，不保存账号密码。
---

# 本地浏览器验收

用当前 Agent 会话已加载的 Playwright MCP 验证已经启动的网页。宿主可以是 Codex、Claude Code、OpenCode、Kimi、Grok 或其他支持 MCP 的 Agent；只按能力判断，不绑定产品名或固定工具前缀。先明确验收对象和可观察结果，再按真实用户路径操作；不要为单次验收在项目里新建浏览器驱动脚本。

待处理列表测试数据的准备和恢复，读 [references/clue-unsubmitted-fixture.md](references/clue-unsubmitted-fixture.md)，并走 `$verify-test-db`。

## 运行前置

1. **读取项目约束**
   先读当前项目的 `AGENTS.md`、README、现有测试说明和相关 diff。项目已有 QA / E2E 命令时继续遵守，本 Skill 只补浏览器验收，不取代它们。

2. **检查当前会话的 MCP 能力**
   必须能实际调用 Playwright MCP 完成导航、页面快照或 DOM 检查、点击或填写以及页面求值。工具名称可以因 Agent 宿主而不同；只看到某个 MCP 配置文件中存在条目，不等于当前会话已经加载成功。

   MCP 不可用时立即标记 `BLOCKED`，读取 [references/playwright-mcp-setup.md](references/playwright-mcp-setup.md)，先识别当前 Agent 和版本，再按该宿主的官方方式给出安装或配置指引。说明可能修改的配置范围和 npm 下载行为后等待用户明确授权。未经授权不得安装、改配置或启动下载；也不得退回临时 CDP、Puppeteer、Selenium 或 Playwright 脚本伪装完成验收。

3. **使用本机统一 profile**
   本机 Playwright MCP 统一通过 `--user-data-dir` 使用 `~/.local/share/playwright-profile`，以便跨 Codex Home 和会话复用登录态。MCP 配置不会经 shell 展开参数时，必须写入解析后的绝对路径，不能把字面量 `~` 直接传给 Playwright；不要同时使用 `--isolated`。该目录包含 Cookie 和会话数据，不读取、展示、提交或清理其中内容，除非用户明确要求。多个 Agent 或 Codex Home 共用它时，同一时刻只启动一个使用该 profile 的浏览器实例，避免 profile 锁冲突。

4. **确认验收契约**
   从用户要求、当前 diff 和项目文档中确定：目标 URL、入口路径、前置数据、操作步骤、预期结果和需要覆盖的视口。能从项目中确定的不要反问；账号、验证码、允许的数据写入或远程目标不明确时再停下确认。

## 通用前检脚本

使用 [scripts/browser_verify.py](scripts/browser_verify.py) 统一做无登录 HTTP 探活，并记录验收前的 Git 状态和浏览器调试产物。脚本不启动前端、不登录、不调用浏览器，也不删除文件。

从当前已加载的 `SKILL.md` 路径定位 Skill 目录，不要硬编码某台机器的安装目录：

```bash
SKILL_DIR="/resolved/path/to/local-browser-verify"
python3 "$SKILL_DIR/scripts/browser_verify.py" prepare \
  --repo . \
  --url "http://127.0.0.1:4173/target-path"
```

PowerShell 中使用工具箱和 Python 的实际安装路径：

```powershell
$AgentToolkitRoot = (Resolve-Path "$HOME\.agent-toolkit").Path
$PythonBin = "$AgentToolkitRoot\.venv\Scripts\python.exe"
& "$PythonBin" `
  "$AgentToolkitRoot\skills\local-browser-verify\scripts\browser_verify.py" `
  prepare --repo . --url "http://127.0.0.1:4173/target-path"
```

- URL 必须来自用户、启动日志或实际监听进程，不猜 `3000`、`5173`、`8888`、`9527`。
- 默认只允许 `localhost`、回环地址和 `0.0.0.0`。用户明确指定远程验收地址时才加 `--allow-remote`。
- 本地自签名 HTTPS 只有在已确认目标正确时才加 `--insecure`。
- `prepare` 输出的 baseline 保存在系统临时目录，不进入项目。
- 返回非成功 HTTP 状态、连接失败或跳转到未授权远程地址时，先解决地址或服务问题，不直接打开浏览器碰运气。

验收结束后比较本轮新增或修改的调试产物：

```bash
python3 "$SKILL_DIR/scripts/browser_verify.py" finish \
  --baseline "/path/printed/by/prepare.json" \
  --repo . \
  --remove-baseline
```

`finish` 报告基线之后新增、修改及消失的产物路径，不判断文件由谁创建。关浏览器、核对并清理本轮产物由 Agent 执行；保留 `modified_artifacts`、验收前已有文件和无关改动。

## 浏览器工作流

1. **探活和后端**
   访问前确认启动进程、实际监听端口和页面请求的 API 环境。网关 503、CORS、首屏 Network Error、白屏进度条优先按环境或代理问题处理。当前 shell 缺少用户明确指定的配置时，按该 shell 的标准方式加载，例如 POSIX shell 的 `source` 或 PowerShell 的点调用（dot-source）；不要猜测、遍历或读取客户端密码。

2. **建立独立会话并完成登录**
   Playwright MCP 是新的浏览器会话，通常复用不到用户自己已打开的窗口。进入目标系统后发现登录页、登录跳转或会话失效时，不直接判定无法验收，应主动让用户选择一种方式：

   - **用户自行登录（优先）**：保持 Agent 打开的 MCP 浏览器窗口可见，请用户在该窗口完成账号、密码、扫码、验证码、MFA 或 SSO，再告知已完成；随后检查页面确实离开登录态并继续验收。
   - **Agent 代为登录**：用户可以在当前对话临时提供用户名和密码。只将其用于本次 MCP 浏览器登录，不复述、不写入命令、脚本、环境变量、项目文件、baseline、截图、日志或最终报告。登录失败时停止连续重试，说明现象后让用户确认凭证或改为自行登录。

   本机统一 profile 已按用户要求持久化认证状态，不再额外写 `storageState`。其他未采用该约定的环境默认不持久化；只有用户明确要求跨会话复用时，才将 `storageState` 写入本机私有且被 Git 忽略的位置，并提前说明其中包含登录会话，不需要后及时清理。用户自己的浏览器已登录，不代表 MCP 会话已登录。

3. **先确认初始状态**
   进入目标页后记录 URL、视口、关键元素、默认筛选和当前记录标识。列表为空时，先核对日期、状态、租户、企业、任务等默认条件，再判断没有数据。不要为了看到历史数据先改业务时间。

4. **按真实路径操作**
   使用页面可见入口点击、填写、提交、切换和滚动，不直接调用内部 API 代替 UI 验收。每个预期拆成独立断言；例如“自动下一条”“播放器开始播放”“下一条表单被重置”是三件事。

5. **等待可观察结果**
   等待目标元素、请求结果、记录 ID、控件属性或业务状态改变，不用固定长时间 sleep 代替同步。需要通用断言时按场景读取 [references/acceptance-patterns.md](references/acceptance-patterns.md)。一次性 DOM 计算通过 MCP 页面求值完成，不在项目中生成脚本。

6. **检查错误和回归面**
   核对本轮操作相关的控制台错误和失败请求，区分页面原有噪声与本次行为产生的问题。响应式改动至少检查受影响的宽屏和窄屏；主题相关改动同时检查浅色和深色。

7. **整理证据并收尾**
   先记录可复核的 DOM、状态值、请求结果或控件属性，再补截图。必须落盘时将截图、HAR 和 trace 写入 `TMP_DIR` 或系统临时目录，不写项目目录。无论 `PASS`、`FAIL`、`MANUAL` 还是 `BLOCKED`，都按下面「收尾」做完再结束。

## 收尾（必须）

验收结束必须主动关掉 MCP 浏览器，并清理确认属于本轮的临时产物。不要把窗口留着占 profile 锁，也不要只跑 `finish` 就收工。

1. **关浏览器**
   调用当前宿主的 Playwright MCP 关闭浏览器（如 `browser_close`）。还有标签就先关标签再关浏览器。关的是 MCP 拉起的实例，不要去杀用户自己的 Chrome，也不要清 `~/.local/share/playwright-profile`。

2. **对产物**
   跑 `browser_verify.py finish ... --remove-baseline`。将 `created_artifacts` 与本轮工具调用记录的输出路径核对，只删除确认由本轮创建的文件；清单也可能包含其他并行测试新增的 `test-results` 或 `playwright-report` 内容，归属不明的保留并说明。目录仅在确认为空后删除，不递归删除整个新增目录。写到 `TMP_DIR` 或系统临时目录的文件按同样的归属规则清理；用户明确要求保留的交付证据保留。

3. **复查**
   再看一次 Git 状态。只允许留下用户原有改动和无关文件；本轮调试产物不得留在仓库里。

中途卡住同样收尾。

## 边界

- 用户只讨论方案时，不打开浏览器、不登录、不启动服务、不写库。
- 不主动启动长期服务；用户要求代为启动时，使用项目原命令并在交付时说明进程状态。
- 页面操作会提交、删除、发送消息、触发外呼或改变共享数据时，按实际副作用先取得授权；“浏览器验收”不是业务写入的通用授权。
- 可以按用户选择临时使用其提供的用户名和密码完成当前登录，但不得回显、传播或持久化账号、密码、Token、Cookie、验证码和未脱敏 PII。
- 填写密码到提交完成前不截图、不录制包含输入值的证据；扫码、验证码、MFA 和 SSO 优先由用户在 MCP 浏览器窗口自行完成。
- 不把截图、视觉印象或“听到了”作为唯一自动化证据。
- Playwright MCP 窗口不是用户本机已有窗口。需要用户自己的页面生效时，明确提醒刷新、重登或清筛选。
- 验收结束必须关闭 MCP 浏览器，并按「收尾」的归属规则清理临时产物；保留项说明原因。

## 结论口径

| 结论 | 条件 |
|---|---|
| `PASS` | 实际走过用户路径，关键前后状态和预期一致，并有 DOM、请求或控件属性可复核 |
| `FAIL` | 实际走过路径，行为与预期不一致，且已记录复现条件和证据 |
| `MANUAL` | 结论依赖听感、用户自己的浏览器窗口、验证码或无法自动观察的感官判断 |
| `BLOCKED` | Playwright MCP 不可用、服务或网关不通，主动询问后用户仍无法或不愿完成必要登录，缺少权限/数据，或操作授权缺失 |

没有登录态时，不得声称覆盖了需登录页面；没有可用数据时，不得声称覆盖了提交、下一条、播放或表单重填；只看代码或 Markdown 时，不得声称页面已经渲染通过。

## 参考

- Playwright MCP 缺失、已配置但未加载或需要安装：[references/playwright-mcp-setup.md](references/playwright-mcp-setup.md)
- 布局、横向滚动、导航、异步、表单、媒体和 Mermaid 的通用断言：[references/acceptance-patterns.md](references/acceptance-patterns.md)
- 待处理列表测试夹具与恢复：[references/clue-unsubmitted-fixture.md](references/clue-unsubmitted-fixture.md)
- 测试库写入计划与确认：`$verify-test-db`
