$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$AgentToolkitRoot = Split-Path -Parent $ScriptDir
$env:AGENT_TOOLKIT_ROOT = $AgentToolkitRoot
$env:SKILLS_ROOT = $AgentToolkitRoot
python "$AgentToolkitRoot\common\entrypoints\jms_cli.py" @args
