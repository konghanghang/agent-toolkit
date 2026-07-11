@echo off
set SCRIPT_DIR=%~dp0
set AGENT_TOOLKIT_ROOT=%SCRIPT_DIR%..
set SKILLS_ROOT=%AGENT_TOOLKIT_ROOT%
python "%AGENT_TOOLKIT_ROOT%\common\entrypoints\sls_cli.py" %*
