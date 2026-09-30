@echo off
REM 构建镜像并导出 tar（适用于在电脑上构建、再导入绿联 NAS 的场景）
REM 需要本机已安装 Docker Desktop。
cd /d "%~dp0.."

echo ==^> 构建镜像 sdcard-backup:1.0
docker build -t sdcard-backup:1.0 .
if errorlevel 1 exit /b 1

echo ==^> 导出镜像到 sdcard-backup-image.tar
docker save sdcard-backup:1.0 -o sdcard-backup-image.tar
if errorlevel 1 exit /b 1

echo.
echo 完成：%cd%\sdcard-backup-image.tar
echo 下一步：在绿联 NAS 的 Docker 应用 - 镜像 - 导入 上传该文件，
echo 然后用项目里的 docker-compose.yml 创建项目（删除 build: 行）。
pause
