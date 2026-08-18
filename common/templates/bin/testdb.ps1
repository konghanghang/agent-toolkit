$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$AgentToolkitRoot = Split-Path -Parent $ScriptDir
$env:AGENT_TOOLKIT_ROOT = $AgentToolkitRoot
python "$AgentToolkitRoot\common\entrypoints\testdb_cli.py" @args
