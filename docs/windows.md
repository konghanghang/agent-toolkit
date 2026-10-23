# Windows 指南

[返回首页](../README.md) · [安装范围与参数](installation.md) · [维护与验证](maintenance.md)

## 前置条件

仓库的真实 Windows CI 使用 `windows-latest` 和 Python 3.11。Windows 本机安装前先准备：

- Git for Windows，并确保 `git.exe` 已加入 `PATH`。
- CPython 3.11，并安装 `py.exe` launcher、`pip` 和 `venv`。
- Windows PowerShell 5.1 的 `powershell.exe`。安装器使用它创建 directory junction，运行时使用它收紧敏感文件 ACL；PowerShell 7 的 `pwsh` 可以作为交互终端，但当前不能替代 `powershell.exe`。
- 将工具箱、用户 Home 以及 Codex/Claude 安装目标放在本机 NTFS 卷。网络盘、FAT/exFAT 和跨卷 junction 不属于已验证范围。

在 PowerShell 中先检查基础依赖：

```powershell
git --version
py -3.11 --version
py -3.11 -m pip --version
py -3.11 -c "import venv"
powershell.exe -NoProfile -Command '$PSVersionTable.PSVersion'
```

安装器与工作运行时的职责，以及各能力的可选依赖，见 [安装器管理范围](installation.md#安装器管理范围)和[外部依赖](installation.md#按能力准备外部依赖)。

## 安装

先按用途选择 profile：家庭电脑使用 `general`；工作电脑使用 `work`，其中已包含通用组件。首次必须显式选择，后续安装沿用本检出的记录；切换分类不会自动卸载。

克隆并还原锁定的外部 Skill：

```powershell
git clone git@github.com:konghanghang/agent-toolkit.git "$HOME\.agent-toolkit"
Set-Location "$HOME\.agent-toolkit"
py -3.11 scripts\update_ui_ux_pro_max.py --from-lock
```

通用安装：

```powershell
py -3.11 "$HOME\.agent-toolkit\scripts\install.py" --profile general --dry-run
py -3.11 "$HOME\.agent-toolkit\scripts\install.py" --profile general
```

工作安装改用以下步骤，并初始化公共运行时；通用安装无需 bootstrap：

```powershell
py -3.11 "$HOME\.agent-toolkit\scripts\install.py" --profile work --dry-run
py -3.11 "$HOME\.agent-toolkit\scripts\install.py" --profile work
py -3.11 "$HOME\.agent-toolkit\common\bootstrap_work.py"
```

工作运行时默认在仓库根目录创建 `.venv`，解释器为 `.venv\Scripts\python.exe`；已有运行时配置保留当前 `PYTHON_BIN` 默认值，迁移时需在交互提示中明确选择项目解释器。凭证和 Session 保留在本机 `~/.local/agent-toolkit/work/`。

## 链接与配置文件

Skill 和 Agent 目录使用 NTFS directory junction，不要求开启 Developer Mode，也不复制目录内容。junction 创建失败时安装器停止并保留错误，不静默回退为普通目录副本。

OpenCode 配置使用普通文件副本，无需文件符号链接权限；安装记录为同目录下的 `.opencode.json.agent-toolkit.json`。重复安装、更新、备份和本机修改保护的完整规则见 [安装结果与配置保护](installation.md#安装结果与配置保护)。

## 命令入口与 ExecutionPolicy

运行 bootstrap 后会同时生成 `.cmd` 和 `.ps1` 入口。PowerShell 中默认使用 `.cmd`；它不受 PowerShell ExecutionPolicy 限制，也不需要修改本机策略：

```powershell
& "$HOME\.agent-toolkit\bin\sls.cmd" --help
& "$HOME\.agent-toolkit\bin\archery.cmd" list-instances-safe
```

只有明确选择运行 `.ps1` 时才检查策略：

```powershell
Get-ExecutionPolicy -List
```

如果 `MachinePolicy` 或 `UserPolicy` 已由组织配置，保留组织策略并继续使用 `.cmd`，不要尝试覆盖。如果没有组织策略、且用户明确希望启用本机 `.ps1` wrapper，可以只在当前用户范围使用 `RemoteSigned`：

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

安装器不会修改 `ExecutionPolicy`。不要设置 `LocalMachine` 范围，不要使用 `Unrestricted`，也不要通过持久化 `Bypass` 绕过策略；无法修改当前用户策略时继续使用 `.cmd`。

## 更新

拉取仓库后，按本检出已记录的 profile 预览和更新安装：

```powershell
git -C "$HOME\.agent-toolkit" pull
py -3.11 "$HOME\.agent-toolkit\scripts\install.py" --dry-run
py -3.11 "$HOME\.agent-toolkit\scripts\install.py"
```

Rules、受管 Playwright 配置与 OpenCode 配置副本需要重新安装；目录 junction 中的 Skill 和 Agent 源文件随仓库更新。

Windows 更新 OpenCode 配置：

```powershell
py -3.11 "$HOME\.agent-toolkit\scripts\install.py" --opencode-only --dry-run
py -3.11 "$HOME\.agent-toolkit\scripts\install.py" --opencode-only
```

Agent 或 Rule 更新后，新开会话或重启客户端；OpenCode 配置变更后执行 `opencode reload` 或重启服务。

## 测试与维护

在已准备的项目环境中使用项目解释器运行测试：

```powershell
& "$HOME\.agent-toolkit\.venv\Scripts\python.exe" -m unittest discover -s tests -p "test_*.py"
```

按改动范围选择检查，完整清单见 [验证与提交检查](maintenance.md#验证与提交检查)。
