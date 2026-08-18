$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$AgentToolkitRoot = Split-Path -Parent $ScriptDir
$env:AGENT_TOOLKIT_ROOT = $AgentToolkitRoot
$env:SKILLS_ROOT = $AgentToolkitRoot
$PreviousPythonUtf8 = $env:PYTHONUTF8
try {
    $env:PYTHONUTF8 = "1"
    python "$AgentToolkitRoot\common\entrypoints\archery_cli.py" @args
} finally {
    if ($null -eq $PreviousPythonUtf8) {
        Remove-Item Env:PYTHONUTF8 -ErrorAction SilentlyContinue
    } else {
        $env:PYTHONUTF8 = $PreviousPythonUtf8
    }
}
