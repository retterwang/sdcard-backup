"""全局配置。

仅保存基础设施级配置（来自环境变量，部署时通过 compose 传入）。
业务设置（扫描间隔、校验开关等）存放在数据库 settings 表中，可在 Web 界面修改。
"""
from __future__ import annotations

import os

APP_NAME = "存储卡备份控制台"
VERSION = "1.0.1"

# 数据目录：SQLite 索引库、配置（必须持久化——增量判断依赖它）
DATA_DIR = os.environ.get("DATA_DIR", "/data")
DB_PATH = os.path.join(DATA_DIR, "app.db")

# 备份根目录：所有存储卡的照片/视频都归集在这里（compose 中映射到 NAS 实际文件夹）
BACKUP_ROOT = os.environ.get("BACKUP_ROOT", "/backup")

# 容器内临时只读挂载点（容器私有，不影响宿主机）
MOUNT_BASE = os.environ.get("MOUNT_BASE", "/mnt/sdbak-cards")

# Web 管理界面端口
PORT = int(os.environ.get("PORT", "8787"))

# 默认业务设置：首次运行写入数据库；之后以数据库为准（界面可改）
DEFAULT_SETTINGS = {
    "scan_interval": 3,      # 设备扫描间隔（秒）
    "mount_delay": 4,        # 检测到插卡后等待多久再挂载（秒，给系统识别留时间）
    "verify": True,          # 复制完成后校验哈希（防拷贝损坏）
    "auto_unmount": True,    # 备份完成后自动卸载（可安全拔卡）
    "auto_accept": False,    # 是否自动接受未注册的卡；False = 只备份白名单内的卡
    "notify_url": "",        # 任务完成/失败时的 Webhook 通知地址（可选）
}
