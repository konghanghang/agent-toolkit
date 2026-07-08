@echo off
set SCRIPT_DIR=%~dp0
set SKILLS_ROOT=%SCRIPT_DIR%..
python "%SKILLS_ROOT%\common\entrypoints\sls_cli.py" %*
