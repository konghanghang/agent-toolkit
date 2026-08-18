@echo off
setlocal
set "PYTHONUTF8=1"
set "SCRIPT_DIR=%~dp0"
set "AGENT_TOOLKIT_ROOT=%SCRIPT_DIR%.."
set "SKILLS_ROOT=%AGENT_TOOLKIT_ROOT%"
python "%AGENT_TOOLKIT_ROOT%\common\entrypoints\jms_cli.py" %*
exit /b %ERRORLEVEL%
