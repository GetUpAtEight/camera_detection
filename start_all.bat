@echo off
chcp 65001 >nul
setlocal

rem ============================================================
rem  Camera Tampering Detection - one-click start (Windows)
rem    algo     FastAPI     http://127.0.0.1:8000
rem    backend  SpringBoot  http://127.0.0.1:8080
rem    frontend Vite dev    http://127.0.0.1:5173
rem  Usage: double click, or:  start_all.bat [--gpu]
rem         --gpu  登录 GPU 服务器、部署算法层并常驻隧道（要求后端 base-url 指向 18010）
rem ============================================================

cd /d "%~dp0"
set "ALGO_DIR=%~dp0algo"
set "BACK_DIR=%~dp0backend"
set "FRONT_DIR=%~dp0frontend"
set "GPU_MODE=0"
if /I "%~1"=="--gpu" set "GPU_MODE=1"
if /I "%~1"=="--remote" set "GPU_MODE=1"

echo [1/3] algo layer ...
if not exist "%ALGO_DIR%\.venv\Scripts\python.exe" goto :mkvenv
goto :checkfront

:mkvenv
echo       creating .venv and installing requirements ...
python -m venv "%ALGO_DIR%\.venv"
if errorlevel 1 goto :fail
"%ALGO_DIR%\.venv\Scripts\python.exe" -m pip install -q -i https://pypi.org/simple -r "%ALGO_DIR%\requirements.txt"
if errorlevel 1 goto :fail

:checkfront
if "%GPU_MODE%"=="1" goto :gpu
echo [2/3] frontend dependencies ...
if exist "%FRONT_DIR%\node_modules" goto :startall
goto :npminstall

:gpu
echo [2/3] frontend dependencies (gpu mode) ...
if exist "%FRONT_DIR%\node_modules" goto :startall

:npminstall
echo       running npm install (first run only) ...
pushd "%FRONT_DIR%"
call npm install
popd
if errorlevel 1 goto :fail

:startall
echo [3/3] starting services ...
if "%GPU_MODE%"=="1" goto :startgpu
rem 本地模式需要 .venv 里有 cv2 / fastapi；缺失时给明确提示，而不是让窗口闪退
"%ALGO_DIR%\.venv\Scripts\python.exe" -c "import cv2, fastapi" >nul 2>&1
if errorlevel 1 (
  echo.
  echo [ERROR] algo\.venv 缺少 cv2 / fastapi。
  echo         离线环境装不了依赖，请改用 GPU 模式：
  echo             start_all.bat --gpu
  echo         或在能联网时先执行：
  echo             algo\.venv\Scripts\python.exe -m pip install -r algo\requirements.txt
  echo.
  pause
  goto :fail
)
start "CTD-Algo-8000" /d "%ALGO_DIR%" cmd /k ".venv\Scripts\python.exe server.py --host 127.0.0.1 --port 8000"
goto :startback

:startgpu
rem 一条 SSH 连接：上传代码 → 重启远端算法层 → 自测 → 常驻转发 18010 → 远端 8010
rem remote.py 依赖 paramiko：优先用算法层 .venv（requirements.txt 已包含），没有再退回系统 python
set "BRIDGE_PY=%ALGO_DIR%\.venv\Scripts\python.exe"
if not exist "%BRIDGE_PY%" set "BRIDGE_PY=python"
start "CTD-GPU-Bridge-18010" /d "%~dp0" cmd /k ""%BRIDGE_PY%" -u deploy\remote.py --deploy --selftest --tunnel"
echo       waiting for tunnel (may take 2-3 min on first auth) ...
timeout /t 20 /nobreak >nul

:startback
rem 后端连哪个算法层：GPU 模式走隧道 18010，本地模式直连 8000。
rem 用环境变量覆盖（Spring Boot 松散绑定 APP_ALGO_BASEURL → app.algo.base-url），
rem 这样两种模式都不用手改 application.yml。
set "APP_ALGO_BASEURL=http://127.0.0.1:18010"
if "%GPU_MODE%"=="0" set "APP_ALGO_BASEURL=http://127.0.0.1:8000"
start "CTD-Backend-8080" /d "%BACK_DIR%" cmd /k "mvn -DskipTests spring-boot:run -Dspring-boot.run.arguments=--server.port=8080"
timeout /t 3 /nobreak >nul
start "CTD-Frontend-5173" /d "%FRONT_DIR%" cmd /k "npm run dev"

echo.
echo Windows are opening. First run may take 1-2 minutes
echo (Maven downloads dependencies, Vite pre-bundles modules).
echo.
if "%GPU_MODE%"=="1" (
  echo   algo     : http://127.0.0.1:18010/health   ^(SSH bridge to GPU server^)
) else (
  echo   algo     : http://127.0.0.1:8000/health
)
echo   backend  : http://127.0.0.1:8080/api/algo/health
echo   frontend : http://localhost:5173
echo.
echo Press Ctrl+C inside a window (or close it) to stop that service.
endlocal
exit /b 0

:fail
echo.
echo [ERROR] startup aborted, see the message above.
endlocal
exit /b 1
