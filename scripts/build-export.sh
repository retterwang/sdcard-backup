#!/usr/bin/env bash
# 构建镜像并导出 tar（适用于在电脑上构建、再导入绿联 NAS 的场景）
# 需要本机已安装 Docker Desktop / Docker Engine。
set -euo pipefail
cd "$(dirname "$0")/.."

IMAGE="sdcard-backup:1.0"
ARCH="$(uname -m)"
echo "==> 构建镜像 $IMAGE （当前架构: $ARCH）"
docker build -t "$IMAGE" .

echo "==> 导出镜像到 sdcard-backup-image.tar"
docker save "$IMAGE" -o sdcard-backup-image.tar

echo ""
echo "完成：$(pwd)/sdcard-backup-image.tar"
echo "下一步：在绿联 NAS 的 Docker 应用 → 镜像 → 导入 上传该文件，"
echo "然后用项目里的 docker-compose.yml 创建项目（删除 build: 行）。"
