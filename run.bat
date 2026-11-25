@echo off
REM TradingAgents Windows 快捷运行脚本（对应 Linux 的 run.sh）
REM 用法: run.bat <代码> [日期 YYYY-MM-DD] [market,news,fundamentals]
REM 前置: 项目根目录有 .env（python-dotenv 会自动加载）；.venv 已建好
setlocal
cd /d %~dp0

set "TICKER=%~1"
if "%TICKER%"=="" (
    echo 用法: run.bat ^<代码^> [日期 YYYY-MM-DD] [market,sentiment,news,fundamentals]
    exit /b 1
)

if "%~2"=="" (
    for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set "DATE=%%i"
) else (
    set "DATE=%~2"
)

set "ANALYSTS=%~3"
if "%ANALYSTS%"=="" set "ANALYSTS=market,news,fundamentals"

set "TRADINGAGENTS_CONVERSATION_ID=tradingagents-%TICKER%@%DATE%"

echo ^>^>^> 分析 %TICKER% @ %DATE%，分析师: %ANALYSTS%
.venv\Scripts\tradingagents.exe --ticker "%TICKER%" --date "%DATE%" --analysts "%ANALYSTS%" --save --html --no-show
if errorlevel 1 exit /b 1
echo ^>^>^> 报告目录: %USERPROFILE%\.tradingagents\logs\reports\
