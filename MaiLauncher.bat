@echo off
@setlocal enabledelayedexpansion
@chcp 936

@REM ���ð汾��
set "VERSION=1.0"

title ����Bot����̨ v%VERSION%

@REM ����Python��Git��������
set "_root=%~dp0"
set "_root=%_root:~0,-1%"
cd "%_root%"


:search_python
cls
if exist "%_root%\python" (
    set "PYTHON_HOME=%_root%\python"
) else if exist "%_root%\venv" (
    call "%_root%\venv\Scripts\activate.bat"
    set "PYTHON_HOME=%_root%\venv\Scripts"
) else (
    echo �����Զ�����Python������...

    where python >nul 2>&1
    if %errorlevel% equ 0 (
        for /f "delims=" %%i in ('where python') do (
            echo %%i | findstr /i /c:"!LocalAppData!\Microsoft\WindowsApps\python.exe" >nul
            if errorlevel 1 (
                echo �ҵ�Python��������%%i
                set "py_path=%%i"
                goto :validate_python
            )
        )
    )
    set "search_paths=%ProgramFiles%\Git*;!LocalAppData!\Programs\Python\Python*"
    for /d %%d in (!search_paths!) do (
        if exist "%%d\python.exe" (
            set "py_path=%%d\python.exe"
            goto :validate_python
        )
    )
    echo û���ҵ�Python������,Ҫ��װ��?
    set /p pyinstall_confirm="������(Y/n): "
    if /i "!pyinstall_confirm!"=="Y" (
        cls
        echo ���ڰ�װPython...
        winget install --id Python.Python.3.13 -e --accept-package-agreements --accept-source-agreements
        if %errorlevel% neq 0 (
            echo ��װʧ�ܣ����ֶ���װPython
            start https://www.python.org/downloads/
            exit /b
        )
        echo ��װ��ɣ�������֤Python...
        goto search_python

    ) else (
        echo ȡ����װPython����������˳�...
        pause >nul
        exit /b
    )

    echo ����δ�ҵ����õ�Python��������
    exit /b 1

    :validate_python
    "!py_path!" --version >nul 2>&1
    if %errorlevel% neq 0 (
        echo ��Ч��Python��������%py_path%
        exit /b 1
    )

    :: ��ȡ��װĿ¼
    for %%i in ("%py_path%") do set "PYTHON_HOME=%%~dpi"
    set "PYTHON_HOME=%PYTHON_HOME:~0,-1%"
)
if not exist "%PYTHON_HOME%\python.exe" (
    echo Python·����֤ʧ�ܣ�%PYTHON_HOME%
    echo ����Python��װ·�����Ƿ���python.exe�ļ�
    exit /b 1
)
echo �ɹ�����Python·����%PYTHON_HOME%



:search_git
cls
if exist "%_root%\tools\git\bin" (
    set "GIT_HOME=%_root%\tools\git\bin"
) else (
    echo �����Զ�����Git...

    where git >nul 2>&1
    if %errorlevel% equ 0 (
        for /f "delims=" %%i in ('where git') do (
            set "git_path=%%i"
            goto :validate_git
        )
    )
    echo ����ɨ�賣����װ·��...
    set "search_paths=!ProgramFiles!\Git\cmd"
    for /f "tokens=*" %%d in ("!search_paths!") do (
        if exist "%%d\git.exe" (
            set "git_path=%%d\git.exe"
            goto :validate_git
        )
    )
    echo û���ҵ�Git��Ҫ��װ��
    set /p confirm="������(Y/N): "
    if /i "!confirm!"=="Y" (
        cls
        echo ���ڰ�װGit...
        set "custom_url=https://ghfast.top/https://github.com/git-for-windows/git/releases/download/v2.48.1.windows.1/Git-2.48.1-64-bit.exe"

        set "download_path=%TEMP%\Git-Installer.exe"

        echo ��������Git��װ��...
        curl -L -o "!download_path!" "!custom_url!"

        if exist "!download_path!" (
            echo ���سɹ�����ʼ��װGit...
            start /wait "" "!download_path!" /SILENT /NORESTART
        ) else (
            echo ����ʧ�ܣ����ֶ���װGit
            start https://git-scm.com/download/win
            exit /b
        )

        del "!download_path!"
        echo ��ʱ�ļ�������

        echo ��װ��ɣ�������֤Git...
        where git >nul 2>&1
        if %errorlevel% equ 0 (
            for /f "delims=" %%i in ('where git') do (
                set "git_path=%%i"
                goto :validate_git
            )
            goto :search_git

        ) else (
            echo ��װ��ɣ���δ�ҵ�Git�����ֶ���װGit
            start https://git-scm.com/download/win
            exit /b
        )

    ) else (
        echo ȡ����װGit����������˳�...
        pause >nul
        exit /b
    )

    echo ����δ�ҵ����õ�Git��
    exit /b 1

    :validate_git
    "%git_path%" --version >nul 2>&1
    if %errorlevel% neq 0 (
        echo ��Ч��Git��%git_path%
        exit /b 1
    )

    :: ��ȡ��װĿ¼
    for %%i in ("%git_path%") do set "GIT_HOME=%%~dpi"
    set "GIT_HOME=%GIT_HOME:~0,-1%"
)

:search_mongodb
cls
sc query | findstr /i "MongoDB" >nul
if !errorlevel! neq 0 (
    echo MongoDB����δ���У��Ƿ������з���
    set /p confirm="�Ƿ�������(Y/N): "
    if /i "!confirm!"=="Y" (
        echo ���ڳ�������MongoDB����...
        powershell -Command "Start-Process -Verb RunAs cmd -ArgumentList '/c net start MongoDB'"
        echo ���ڵȴ�MongoDB��������...
		echo ��������������ȴ�...
		timeout /t 30 >nul
        sc query | findstr /i "MongoDB" >nul
        if !errorlevel! neq 0 (
            echo MongoDB��������ʧ�ܣ�������û�а�װ��Ҫ��װ��
            set /p install_confirm="������װ��(Y/N): "
            if /i "!install_confirm!"=="Y" (
                echo ���ڰ�װMongoDB...
                winget install --id MongoDB.Server -e --accept-package-agreements --accept-source-agreements
                echo ��װ��ɣ���������MongoDB����...
                net start MongoDB
                if !errorlevel! neq 0 (
                    echo ����MongoDB����ʧ�ܣ����ֶ�����
                    exit /b
                ) else (
                    echo MongoDB�����ѳɹ�����
                )
            ) else (
                echo ȡ����װMongoDB����������˳�...
                pause >nul
				exit /b
            )
        )
    ) else (
        echo "���棺MongoDB����δ���У�������MaiMBot�޷��������ݿ⣡"
    )
) else (
    echo MongoDB����������
)

@REM set "GIT_HOME=%_root%\tools\git\bin"
set "PATH=%PYTHON_HOME%;%GIT_HOME%;%PATH%"

:install_maim
if not exist "!_root!\bot.py" (
    cls
    echo ���ƺ�û�а�װ����Bot��Ҫ��װ�ڵ�ǰĿ¼��
    set /p confirm="������(Y/N): "
    if /i "!confirm!"=="Y" (
        echo Ҫʹ��Git����������
        set /p proxy_confirm="������(Y/N): "
        if /i "!proxy_confirm!"=="Y" (
            echo ���ڰ�װ����Bot...
            git clone https://ghfast.top/https://github.com/SengokuCola/MaiMBot
        ) else (
            echo ���ڰ�װ����Bot...
            git clone https://github.com/SengokuCola/MaiMBot
        )
        xcopy /E /H /I MaiMBot . >nul 2>&1
        rmdir /s /q MaiMBot
        git checkout main-fix

        echo ��װ��ɣ����ڰ�װ����...
        python -m pip config set global.index-url https://mirrors.aliyun.com/pypi/simple
        python -m pip install virtualenv
        python -m virtualenv venv
        call venv\Scripts\activate.bat
        python -m pip install -r requirements.txt

        echo ��װ��ɣ�Ҫ�༭�����ļ���
        set /p edit_confirm="������(Y/N): "
        if /i "!edit_confirm!"=="Y" (
            goto config_menu
        ) else (
            echo ȡ���༭�����ļ�����������������˵�...
        )
    )
)


@REM git��ȡ��ǰ��֧���������ڱ�����
for /f "delims=" %%b in ('git symbolic-ref --short HEAD 2^>nul') do (
    set "BRANCH=%%b"
)

@REM ���ݲ�ͬ��֧������֧���ַ���ʹ�ò�ͬ��ɫ
echo ��֧��: %BRANCH%
if "!BRANCH!"=="main" (
    set "BRANCH_COLOR=[92m"
) else if "!BRANCH!"=="main-fix" (
    set "BRANCH_COLOR=[91m"
@REM ) else if "%BRANCH%"=="stable-dev" (
@REM     set "BRANCH_COLOR=[96m"
) else (
    set "BRANCH_COLOR=[93m"
)

@REM endlocal & set "BRANCH_COLOR=%BRANCH_COLOR%"

:check_is_venv
echo ���ڼ�����⻷��״̬...
if exist "%_root%\config\no_venv" (
    echo ��⵽no_venv,�������⻷�����
    goto menu
)

:: �������
if defined VIRTUAL_ENV (
    goto menu
)

echo =====================================
echo ���⻷����⾯�棺
echo ��ǰʹ��ϵͳPython·����!PYTHON_HOME!
echo δ��⵽��������⻷����

:env_interaction
echo =====================================
echo ��ѡ�������
echo 1 - ����������Venv���⻷��
echo 2 - ����/����Conda���⻷��
echo 3 - ��ʱ�������μ��
echo 4 - �����������⻷�����
set /p choice="������ѡ��(1-4): "

if "!choice!"=="4" (
	echo Ҫ�����������⻷�������
    set /p no_venv_confirm="������(Y/N): ....."
    if /i "!no_venv_confirm!"=="Y" (
		echo 1 > "%_root%\config\no_venv"
		echo �Ѵ���no_venv�ļ�
		pause >nul
		goto menu
	) else (
        echo ȡ���������⻷����飬�����������...
        pause >nul
        goto env_interaction
    )
)

if "!choice!"=="3" (
    echo ���棺ʹ��ϵͳ�������ܵ���������ͻ��
    timeout /t 2 >nul
    goto menu
)

if "!choice!"=="2" goto handle_conda
if "!choice!"=="1" goto handle_venv

echo ��Ч�����룬������1-4֮�������
timeout /t 2 >nul
goto env_interaction

:handle_venv
python -m pip config set global.index-url https://mirrors.aliyun.com/pypi/simple
echo ���ڳ�ʼ��Venv����...
python -m pip install virtualenv || (
    echo ��װ����ʧ�ܣ������룺!errorlevel!
    pause
    goto env_interaction
)
echo �������⻷������venv
    python -m virtualenv venv || (
    echo ��������ʧ�ܣ������룺!errorlevel!
    pause
    goto env_interaction
)

call venv\Scripts\activate.bat
echo �Ѽ���Venv����
echo Ҫ��װ������
set /p install_confirm="������(Y/N): "
if /i "!install_confirm!"=="Y" (
    goto update_dependencies
)
goto menu

:handle_conda
where conda >nul 2>&1 || (
    echo δ��⵽conda������ԭ��
    echo 1. δ��װMiniconda
    echo 2. conda�����쳣
    timeout /t 10 >nul
    goto env_interaction
)

:conda_menu
echo ��ѡ��Conda������
echo 1 - �����»���
echo 2 - �������л���
echo 3 - �����ϼ��˵�
set /p choice="������ѡ��(1-3): "

if "!choice!"=="3" goto env_interaction
if "!choice!"=="2" goto activate_conda
if "!choice!"=="1" goto create_conda

echo ��Ч�����룬������1-3֮�������
timeout /t 2 >nul
goto conda_menu

:create_conda
set /p "CONDA_ENV=�������»������ƣ�"
if "!CONDA_ENV!"=="" (
    echo �������Ʋ���Ϊ�գ�
    goto create_conda
)
conda create -n !CONDA_ENV! python=3.13 -y || (
    echo ��������ʧ�ܣ������룺!errorlevel!
    timeout /t 10 >nul
    goto conda_menu
)
goto activate_conda

:activate_conda
set /p "CONDA_ENV=������Ҫ����Ļ������ƣ�"
call conda activate !CONDA_ENV! || (
    echo ����ʧ�ܣ�����ԭ��
    echo 1. ����������
    echo 2. conda�����쳣
    pause
    goto conda_menu
)
echo �ɹ�����conda������!CONDA_ENV!
echo Ҫ��װ������
set /p install_confirm="������(Y/N): "
if /i "!install_confirm!"=="Y" (
    goto update_dependencies
)
:menu
@chcp 936
cls
echo ����Bot����̨ v%VERSION%  ��ǰ��֧: %BRANCH_COLOR%%BRANCH%[0m
echo ��ǰPython����: [96m!PYTHON_HOME![0m
echo ======================
echo 1. ���²���������Bot (Ĭ��)
echo 2. ֱ����������Bot
echo 3. �����������ý���
echo 4. ���������湤����
echo 5. �˳�
echo ======================

set /p choice="������ѡ������ (1-5)�����»س���ѡ��: "

if "!choice!"=="" set choice=1

if "!choice!"=="1" goto update_and_start
if "!choice!"=="2" goto start_bot
if "!choice!"=="3" goto config_menu
if "!choice!"=="4" goto tools_menu
if "!choice!"=="5" exit /b

echo ��Ч�����룬������1-5֮�������
timeout /t 2 >nul
goto menu

:config_menu
@chcp 936
cls
if not exist config/bot_config.toml (
    copy /Y "template\bot_config_template.toml" "config\bot_config.toml"

)
if not exist .env.prod (
    copy /Y "template\.env.prod" ".env.prod"
)

start python webui.py

goto menu


:tools_menu
@chcp 936
cls
echo ����ʱ�й�����  ��ǰ��֧: %BRANCH_COLOR%%BRANCH%[0m
echo ======================
echo 1. ��������
echo 2. �л���֧
echo 3. ���õ�ǰ��֧
echo 4. ���������ļ�
echo 5. ѧϰ�µ�֪ʶ��
echo 6. ��֪ʶ���ļ���
echo 7. �������˵�
echo ======================

set /p choice="������ѡ������: "
if "!choice!"=="1" goto update_dependencies
if "!choice!"=="2" goto switch_branch
if "!choice!"=="3" goto reset_branch
if "!choice!"=="4" goto update_config
if "!choice!"=="5" goto learn_new_knowledge
if "!choice!"=="6" goto open_knowledge_folder
if "!choice!"=="7" goto menu

echo ��Ч�����룬������1-6֮�������
timeout /t 2 >nul
goto tools_menu

:update_dependencies
cls
echo ���ڸ�������...
python -m pip config set global.index-url https://mirrors.aliyun.com/pypi/simple
python.exe -m pip install -r requirements.txt

echo ����������ɣ�����������ع�����˵�...
pause
goto tools_menu

:switch_branch
cls
echo �����л���֧...
echo ��ǰ��֧: %BRANCH%
@REM echo ���÷�֧: main, debug, stable-dev
echo 1. �л���[92mmain[0m
echo 2. �л���[91mmain-fix[0m
echo ������Ҫ�л����ķ�֧:
set /p branch_name="��֧��: "
if "%branch_name%"=="" set branch_name=main
if "%branch_name%"=="main" (
    set "BRANCH_COLOR=[92m"
) else if "%branch_name%"=="main-fix" (
    set "BRANCH_COLOR=[91m"
@REM ) else if "%branch_name%"=="stable-dev" (
@REM     set "BRANCH_COLOR=[96m"
) else if "%branch_name%"=="1" (
    set "BRANCH_COLOR=[92m"
    set "branch_name=main"
) else if "%branch_name%"=="2" (
    set "BRANCH_COLOR=[91m"
    set "branch_name=main-fix"
) else (
    echo ��Ч�ķ�֧��, ����������
    timeout /t 2 >nul
    goto switch_branch
)

echo �����л�����֧ %branch_name%...
git checkout %branch_name%
echo ��֧�л���ɣ���ǰ��֧: %BRANCH_COLOR%%branch_name%[0m
set "BRANCH=%branch_name%"
echo ����������ع�����˵�...
pause >nul
goto tools_menu


:reset_branch
cls
echo �������õ�ǰ��֧...
echo ��ǰ��֧: !BRANCH!
echo ȷ��Ҫ���õ�ǰ��֧��
set /p confirm="������(Y/N): "
if /i "!confirm!"=="Y" (
    echo �������õ�ǰ��֧...
    git reset --hard !BRANCH!
    echo ��֧������ɣ�����������ع�����˵�...
) else (
    echo ȡ�����õ�ǰ��֧������������ع�����˵�...
)
pause >nul
goto tools_menu


:update_config
cls
echo ���ڸ��������ļ�...
echo ��ȷ���ѱ�����Ҫ���ݣ��������޸ĵ�ǰ�����ļ���
echo �����밴Y��ȡ���밴�����...
set /p confirm="������(Y/N): "
if /i "!confirm!"=="Y" (
    echo ���ڸ��������ļ�...
    python.exe config\auto_update.py
    echo �����ļ�������ɣ�����������ع�����˵�...
) else (
    echo ȡ�����������ļ�������������ع�����˵�...
)
pause >nul
goto tools_menu

:learn_new_knowledge
cls
echo ����ѧϰ�µ�֪ʶ��...
echo ��ȷ���ѱ�����Ҫ���ݣ��������޸ĵ�ǰ֪ʶ�⡣
echo �����밴Y��ȡ���밴�����...
set /p confirm="������(Y/N): "
if /i "!confirm!"=="Y" (
    echo ����ѧϰ�µ�֪ʶ��...
    python.exe src\plugins\zhishi\knowledge_library.py
    echo ѧϰ��ɣ�����������ع�����˵�...
) else (
    echo ȡ��ѧϰ�µ�֪ʶ�⣬����������ع�����˵�...
)
pause >nul
goto tools_menu

:open_knowledge_folder
cls
echo ���ڴ�֪ʶ���ļ���...
if exist data\raw_info (
    start explorer data\raw_info
) else (
    echo ֪ʶ���ļ��в����ڣ�
    echo ���ڴ����ļ���...
    mkdir data\raw_info
    timeout /t 2 >nul
)
goto tools_menu


:update_and_start
cls
:retry_git_pull
git pull > temp.log 2>&1
findstr /C:"detected dubious ownership" temp.log >nul
if %errorlevel% equ 0 (
    echo ��⵽�ֿ�Ȩ�����⣬�����Զ��޸�...
    git config --global --add safe.directory "%cd%"
    echo ��������⣬��������git pull...
    del temp.log
    goto retry_git_pull
)
del temp.log
echo ���ڸ�������...
python -m pip config set global.index-url https://mirrors.aliyun.com/pypi/simple
python -m pip install -r requirements.txt && cls

echo ��ǰ��������:
echo HTTP_PROXY=%HTTP_PROXY%
echo HTTPS_PROXY=%HTTPS_PROXY%

echo Disable Proxy...
set HTTP_PROXY=
set HTTPS_PROXY=
set no_proxy=0.0.0.0/32

REM chcp 65001
python bot.py
echo.
echo Bot��ֹͣ���У���������������˵�...
pause >nul
goto menu

:start_bot
cls
echo ���ڸ�������...
python -m pip config set global.index-url https://mirrors.aliyun.com/pypi/simple
python -m pip install -r requirements.txt && cls

echo ��ǰ��������:
echo HTTP_PROXY=%HTTP_PROXY%
echo HTTPS_PROXY=%HTTPS_PROXY%

echo Disable Proxy...
set HTTP_PROXY=
set HTTPS_PROXY=
set no_proxy=0.0.0.0/32

REM chcp 65001
python bot.py
echo.
echo Bot��ֹͣ���У���������������˵�...
pause >nul
goto menu


:open_dir
start explorer "%cd%"
goto menu
