$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SkillsRoot = Split-Path -Parent $ScriptDir
python "$SkillsRoot\common\entrypoints\archery_cli.py" @args
