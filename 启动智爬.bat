@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   智爬 · AI 网页数据采集（Web 模式）
echo.
echo   ⚠ 重要：请勿关闭本窗口！
echo   本窗口是服务器，关闭它会断网。
echo ============================================
echo.
echo 正在准备...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8550" ^| findstr "LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
    echo   已清理旧服务进程 PID %%a
)
echo 正在启动，浏览器会自动打开 http://127.0.0.1:8550
echo 如果浏览器没自动打开，请手动访问该地址。
echo.
D:\python\python.exe run_web.py > 运行日志.txt 2>&1
echo.
echo 服务已停止。如果上方出现红色错误，请把项目里的「运行日志.txt」内容发给我。
pause >nul