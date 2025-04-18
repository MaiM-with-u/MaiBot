@echo off
CHCP 65001 > nul
setlocal enabledelayedexpansion

REM 查找venv虚拟环境
set "venv_path=%~dp0venv\Scripts\activate.bat"
if not exist "%venv_path%" (
    echo 错误: 未找到虚拟环境，请确保venv目录存在
    pause
    exit /b 1
)

REM 激活虚拟环境
call "%venv_path%"
if %ERRORLEVEL% neq 0 (
    echo 错误: 虚拟环境激活失败
    pause
    exit /b 1
)

REM 运行预处理脚本
python "%~dp0raw_data_preprocessor.py"
if %ERRORLEVEL% neq 0 (
    echo 错误: raw_data_preprocessor.py 执行失败
    pause
    exit /b 1
)

REM 运行信息提取脚本
python "%~dp0info_extraction.py"
if %ERRORLEVEL% neq 0 (
    echo 错误: info_extraction.py 执行失败
    pause
    exit /b 1
)

REM 运行OpenIE导入脚本
python "%~dp0import_openie.py"
if %ERRORLEVEL% neq 0 (
    echo 错误: import_openie.py 执行失败
    pause
    exit /b 1
)

echo 所有处理步骤完成!
pause