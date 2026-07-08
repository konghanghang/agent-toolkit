$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SkillsRoot = Split-Path -Parent $ScriptDir
python "$SkillsRoot\common\entrypoints\sls_cli.py" @args
