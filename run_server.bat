@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    py -3.12 -m venv .venv
    if errorlevel 1 exit /b 1
)
.venv\Scripts\python.exe -m pip install .
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m dhan_mcp_server %*
