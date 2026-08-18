@echo off
set SCRIPT_DIR=%~dp0
set AGENT_TOOLKIT_ROOT=%SCRIPT_DIR%..
python "%AGENT_TOOLKIT_ROOT%\common\entrypoints\testdb_cli.py" %*
