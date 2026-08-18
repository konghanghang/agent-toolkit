@echo off
setlocal
set "PYTHONUTF8=1"
set "SCRIPT_DIR=%~dp0"
set "AGENT_TOOLKIT_ROOT=%SCRIPT_DIR%.."
python "%AGENT_TOOLKIT_ROOT%\common\entrypoints\testdb_cli.py" %*
exit /b %ERRORLEVEL%
