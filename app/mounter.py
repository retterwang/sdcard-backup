"""只读挂载管理。

安全原则：本程序永远不向存储卡写入任何数据——挂载一律 ro（只读）。

挂载策略（按顺序尝试）：
1. 容器内自己只读挂载（首选：设备未被宿主占用时最干净）
2. 若宿主（绿联 UGOS）已自动挂载了该卡：
   - 宿主挂载点恰好也映射进了容器 → 直接读取该路径（回退）
   - 否则报错，并在错误信息中给出处理建议
"""
from __future__ import annotations

import logging
import os
import subprocess
import time
from dataclasses import dataclass

from . import config

log = logging.getLogger("mounter")


@dataclass
class Mount:
    node: str
    path: str = ""
    mode: str = "none"      # self-ro / host / none
    by_us: bool = False     # 是否由本程序挂载（决定是否需要卸载）
    error: str = ""


def _run(cmd: list) -> tuple:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return r.returncode, (r.stderr or r.stdout or "").strip()
    except Exception as e:
        return 1, str(e)


def _umount(path: str, retries: int = 3):
    for i in range(retries):
        code, err = _run(["umount", path])
        if code == 0:
            return True
        time.sleep(0.8)
    # 兜底：lazy umount（设备已拔出时残留挂载点用）
    code, err = _run(["umount", "-l", path])
    if code != 0:
        log.warning("卸载 %s 失败: %s", path, err)
    return code == 0


def ensure_mounted(dev, base: str = None) -> Mount:
    """确保设备已可读，返回可用的挂载路径。失败时 Mount.error 有说明。"""
    base = base or config.MOUNT_BASE
    target = os.path.join(base, dev.part)

    # 清理上次异常退出可能残留的挂载点
    if os.path.ismount(target):
        _umount(target)

    try:
        os.makedirs(target, exist_ok=True)
    except OSError as e:
        return Mount(node=dev.node, error=f"无法创建挂载点 {target}: {e}")

    errs = []
    for opts in ("ro,nodev,nosuid,noexec", "ro,nodev", "ro"):
        code, err = _run(["mount", "-o", opts, "--", dev.node, target])
        if code == 0:
            # 挂上后再确认真的可读
            try:
                os.listdir(target)
                log.info("只读挂载成功: %s -> %s", dev.node, target)
                return Mount(node=dev.node, path=target, mode="self-ro", by_us=True)
            except OSError as e:
                errs.append(f"挂载后不可读: {e}")
                _umount(target)
                break
        errs.append(err)
        low = err.lower()
        if "already mounted" in low or "busy" in low:
            # 已被宿主占用，重试无意义，直接走回退
            break

    # 回退：宿主的挂载点在容器内可见时，直接读取
    for mp in dev.mountpoints:
        if os.path.isdir(mp):
            try:
                os.listdir(mp)
                log.info("使用宿主已挂载路径: %s", mp)
                return Mount(node=dev.node, path=mp, mode="host", by_us=False)
            except OSError:
                continue

    hint = (
        "无法挂载存储卡。若绿联系统已自动挂载该卡，请在「文件管理→外部设备」里"
        "先弹出，或把宿主挂载点（如 /mnt/@usb）映射进容器后重试。"
    )
    detail = "；".join(dict.fromkeys([e for e in errs if e]))[:400]
    return Mount(node=dev.node, error=f"{hint} 原始错误: {detail}")


def release(m: Mount):
    if m and m.by_us and m.path:
        _umount(m.path, retries=2)
