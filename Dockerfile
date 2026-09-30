# ─────────────────────────────────────────────────────────────
# 基础镜像：国内网络无法直连 Docker Hub（registry-1.docker.io 会超时），
# 默认使用 DaoCloud 公共代理。备用源（改 BASE_IMAGE 即可，实测均可达）：
#   docker.m.daocloud.io/library/python:3.12-slim-bookworm          （默认）
#   docker.1panel.live/library/python:3.12-slim-bookworm
#   swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/library/python:3.12-slim-bookworm
#   python:3.12-slim-bookworm                                       （官方，海外/有代理时用）
# 覆盖方式：docker compose 的 build.args.BASE_IMAGE，或 --build-arg BASE_IMAGE=...
# ─────────────────────────────────────────────────────────────
ARG BASE_IMAGE=docker.m.daocloud.io/library/python:3.12-slim-bookworm
FROM ${BASE_IMAGE}

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai

# Debian 源换成清华镜像（国内构建更快更稳，含重试）
# util-linux: lsblk / mount / umount
# exfatprogs + exfat-fuse: 读取 exFAT 存储卡（大容量相机卡的主流格式）
# ntfs-3g: 兼容 NTFS 格式的卡
RUN set -eux; \
    for f in /etc/apt/sources.list /etc/apt/sources.list.d/debian.sources; do \
      if [ -f "$f" ]; then sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' "$f"; fi; \
    done; \
    apt-get -o Acquire::Retries=3 update; \
    apt-get install -y --no-install-recommends \
      util-linux exfatprogs exfat-fuse ntfs-3g; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir \
      -i https://pypi.tuna.tsinghua.edu.cn/simple \
      -r requirements.txt

COPY app ./app

EXPOSE 8787
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD python -c "import os,urllib.request as u;u.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8787')+'/api/health',timeout=3)" || exit 1

CMD ["python", "-m", "app"]
