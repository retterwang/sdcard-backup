"""设备检测：轮询 lsblk 发现/移除外接存储（USB 读卡器 或 NAS 内置 SD 槽）。

为什么用轮询而不是 udev 事件：
- 容器内拿不到宿主 udev 的 netlink 事件（除非把宿主 udev 搬进容器，复杂且脆弱）；
- lsblk 每次执行仅毫秒级开销，3 秒轮询对 NAS 完全无感；
- 只需挂载 /dev 与 /sys 即可工作，不依赖 systemd/udev 环境。

卡片身份识别优先级（决定“是不是同一张卡”）：
1. SD 卡 CID 序列号（内置读卡器 mmcblk，最可靠——就是卡本身的序列号）
2. 分区文件系统 UUID（USB 读卡器场景——注意 USB 读卡器自身序列号是读卡器的，不是卡的）
3. 分区 PARTUUID
4. 卷标 + 容量（兜底，仅用于展示）

v1.0.1 加固要点（针对容器环境的检测盲区，2026-09-30 实证）：
- 容器内通常看不到宿主的 udev 数据库（/run/udev），lsblk 会丢失 FSTYPE/UUID/LABEL，
  甚至丢失 TRAN（总线）信息 → 设备会被过滤规则误排除，界面表现为"未检测到存储卡"；
- 对策①：文件系统信息缺失时，用 `blkid` 直接读设备补齐（带 60s 缓存，避免频繁探测 I/O）；
- 对策②：过滤放宽——可移动磁盘上 size>0 且非 swap/LVM/RAID 等类型的分区一律列为候选，
  宁可多列（白名单把关），不可因信息缺失而静默消失；
- 对策③：tran 为空时用 sysfs 路径兜底判断总线（/sys/block/X/device 真实路径含 usb/mmc）；
- 对策④：首次扫描与设备集合变化时，打印每个磁盘的判定原因（docker logs 可直接排查）；
- 对策⑤：提供 diagnose() 运行环境自检（配合界面「运行环境自检」按钮）。
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field

from . import media

log = logging.getLogger("detector")

SUPPORTED_FS = {"exfat", "vfat", "ntfs", "ext4", "ext3", "ext2"}

# 明确不属于"可备份存储卡"的文件系统（即使出现在可移动磁盘上）
EXCLUDED_FS = {
    "swap", "linux_raid_member", "lvm2_member", "crypto_luks", "zfs_member",
    "isw_raid_member", "ddf_raid_member", "bitlocker", "vmfs", "udf",
}

LSBLK_COLS = (
    "NAME,PKNAME,TYPE,SIZE,FSTYPE,FSVER,LABEL,UUID,PARTUUID,"
    "MODEL,SERIAL,TRAN,VENDOR,RM,RO,MOUNTPOINTS"
)

_PROBE_CACHE = {}   # (node, size) -> (ts, info)


def _read_sys(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _run_lsblk() -> dict:
    out = subprocess.run(
        ["lsblk", "-J", "-b", "-o", LSBLK_COLS],
        capture_output=True, text=True, timeout=15,
    )
    if out.returncode != 0:
        raise RuntimeError(f"lsblk 执行失败: {out.stderr.strip()[:300]}")
    return json.loads(out.stdout or "{}")


def _norm_mounts(node: dict) -> list:
    mp = node.get("mountpoints")
    if mp is None:
        v = node.get("mountpoint")
        mp = [v] if v else []
    return [m for m in (mp or []) if m]


def _blkid_probe(node: str) -> dict:
    """用 blkid 直接读设备，获取 文件系统类型/UUID/卷标/PARTUUID（不依赖 udev）。"""
    try:
        r = subprocess.run(["blkid", "-o", "export", node],
                           capture_output=True, text=True, timeout=10)
    except Exception:
        return {}
    info = {}
    for line in (r.stdout or "").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            info[k.strip().lower()] = v.strip()
    out = {}
    if info.get("type"):
        out["fstype"] = info["type"].lower()
    if info.get("uuid"):
        out["fs_uuid"] = info["uuid"]
    if info.get("label"):
        out["label"] = info["label"]
    if info.get("partuuid"):
        out["partuuid"] = info["partuuid"]
    return out


def _blkid_probe_cached(node: str, size: int = 0) -> dict:
    """带缓存的探测（同一设备 60 秒内不重复读盘）。"""
    key = (node, int(size or 0))
    now = time.time()
    hit = _PROBE_CACHE.get(key)
    if hit and now - hit[0] < 60:
        return hit[1]
    info = _blkid_probe(node)
    _PROBE_CACHE[key] = (now, info)
    return info


def _bus_hint(disk_name: str, realpath) -> str:
    """不依赖 udev 的总线判断：检查 /sys/block/<disk>/device 的真实路径。"""
    if not disk_name:
        return ""
    try:
        p = str(realpath("/sys/block/%s/device" % disk_name))
    except Exception:
        return ""
    if "/usb" in p:
        return "usb"
    if "mmc" in p:
        return "mmc"
    return ""


def _is_removable(disk: dict, realpath=os.path.realpath, sysfs=_read_sys):
    """判断是否外接/可移动设备。返回 (bool, 原因说明)。"""
    name = str(disk.get("name") or "")
    tran = str(disk.get("tran") or "").lower()
    rm = bool(disk.get("rm"))
    if name.startswith("mmcblk"):
        # mmcblk 可能是「SD 卡槽（可移动）」也可能是「内置 eMMC 系统盘（不可移动，
        # 如绿联 NAS 的 mmcblk0）」，必须用 removable 标记 + device/type 精确区分，
        # 否则会把系统盘误报为存储卡。
        typ = sysfs("/sys/block/%s/device/type" % name).strip().upper()
        if rm or typ == "SD":
            return True, "SD/MMC 可移动存储（type=%s rm=%d）" % (typ or "?", int(rm))
        return False, "内置 eMMC/不可移动（type=%s rm=0）" % (typ or "?")
    if tran in ("usb", "mmc", "sdio"):
        return True, f"总线={tran}"
    if rm:
        return True, "可移动标记（rm）"
    bus = _bus_hint(name, realpath)
    if bus:
        return True, f"sysfs 路径判定总线={bus}"
    return False, "非外接设备（tran/rm/sysfs 均不支持）"


@dataclass
class Device:
    """一张候选存储卡（取分区表中最合适的主分区）。"""

    disk: str
    part: str
    node: str
    fstype: str
    label: str = ""
    fs_uuid: str = ""
    partuuid: str = ""
    size: int = 0
    tran: str = ""
    model: str = ""
    vendor: str = ""
    reader_serial: str = ""
    mountpoints: list = field(default_factory=list)
    is_mmc: bool = False
    mmc_serial: str = ""
    mmc_name: str = ""
    other_parts: list = field(default_factory=list)

    @property
    def card_id(self) -> str:
        if self.is_mmc and self.mmc_serial:
            return f"mmc:{self.mmc_serial}:{self.mmc_name or ''}"
        if self.fs_uuid:
            return f"fs:{self.fs_uuid}"
        if self.partuuid:
            return f"part:{self.partuuid}"
        return f"ld:{self.label or 'nolabel'}:{self.size}"

    @property
    def id_candidates(self) -> list:
        """用于匹配白名单的所有候选 ID（兼容注册后 UUID 形态变化）。"""
        c = [self.card_id]
        if self.fs_uuid:
            c.append(f"fs:{self.fs_uuid}")
        if self.partuuid:
            c.append(f"part:{self.partuuid}")
        return list(dict.fromkeys(c))

    @property
    def display(self) -> str:
        bits = [self.label or self.model or self.reader_serial or "存储卡"]
        bits.append(self.fstype or "?")
        bits.append(media.fmt_bytes(self.size))
        return " · ".join(bits)

    def to_dict(self) -> dict:
        return {
            "card_id": self.card_id,
            "node": self.node,
            "disk": self.disk,
            "part": self.part,
            "fstype": self.fstype,
            "label": self.label,
            "fs_uuid": self.fs_uuid,
            "size": self.size,
            "tran": self.tran,
            "model": self.model,
            "reader_serial": self.reader_serial,
            "mountpoints": self.mountpoints,
            "is_mmc": self.is_mmc,
            "display": self.display,
        }


def analyze(raw: dict, sysfs=_read_sys, probe=_blkid_probe_cached, realpath=os.path.realpath):
    """解析 lsblk JSON → (devices, notes)。

    notes 为每个顶层磁盘的判定记录（含跳过原因与各分区状态），
    用于日志输出与界面「运行环境自检」。
    """
    devices, notes = [], []
    for disk in raw.get("blockdevices", []) or []:
        name = str(disk.get("name") or "")
        if not name:
            continue
        removable, why = _is_removable(disk, realpath, sysfs)
        note = {
            "name": name,
            "tran": str(disk.get("tran") or ""),
            "removable": removable,
            "why": why,
            "parts": [],
            "decision": "",
        }
        notes.append(note)
        if not removable:
            note["decision"] = "跳过：" + why
            continue

        children = disk.get("children") or []
        parts = [c for c in children if int(c.get("size") or 0) > 0]
        if not parts and str(disk.get("fstype") or "").strip():
            parts = [disk]  # 无分区表的“超级软盘”形态
        if not parts:
            note["decision"] = "跳过：没有可用分区"
            continue

        cands = []
        for c in parts:
            cname = str(c.get("name") or "")
            node = "/dev/" + cname
            size = int(c.get("size") or 0)
            fs = str(c.get("fstype") or "").strip().lower()
            fs_uuid = str(c.get("uuid") or "").strip()
            label = str(c.get("label") or c.get("partlabel") or "").strip()
            partuuid = str(c.get("partuuid") or "").strip()

            # 文件系统信息缺失（容器无 udev 数据时常见）→ 直接探测补齐
            if (not fs or not fs_uuid) and fs not in EXCLUDED_FS and probe:
                info = probe(node, size) or {}
                fs = fs or str(info.get("fstype") or "").lower()
                fs_uuid = fs_uuid or str(info.get("fs_uuid") or "")
                label = label or str(info.get("label") or "")
                partuuid = partuuid or str(info.get("partuuid") or "")

            excluded = fs in EXCLUDED_FS
            note["parts"].append({
                "name": cname, "size": size, "fstype": fs, "excluded": excluded,
            })
            if excluded:
                continue
            cands.append({
                "part": cname, "node": node, "size": size, "fstype": fs,
                "fs_uuid": fs_uuid, "label": label, "partuuid": partuuid,
                "mountpoints": _norm_mounts(c),
            })

        if not cands:
            note["decision"] = "跳过：分区均为 swap/RAID/LVM 等不支持类型"
            continue

        # 主分区：优先常见相机卡文件系统，其次容量最大
        primary = max(cands, key=lambda x: (1 if x["fstype"] in SUPPORTED_FS else 0, x["size"]))
        is_mmc = name.startswith("mmcblk")
        bus = _bus_hint(name, realpath)
        dev = Device(
            disk=name,
            part=primary["part"],
            node=primary["node"],
            fstype=primary["fstype"],
            label=primary["label"],
            fs_uuid=primary["fs_uuid"],
            partuuid=primary["partuuid"],
            size=primary["size"],
            tran=str(disk.get("tran") or "").lower() or bus,
            model=str(disk.get("model") or "").strip(),
            vendor=str(disk.get("vendor") or "").strip(),
            reader_serial=str(disk.get("serial") or "").strip(),
            mountpoints=primary["mountpoints"],
            is_mmc=is_mmc,
        )
        if is_mmc:
            dev.mmc_serial = sysfs(f"/sys/block/{name}/device/serial")
            dev.mmc_name = sysfs(f"/sys/block/{name}/device/name")
        dev.other_parts = [c["part"] for c in cands if c["part"] != primary["part"]]
        note["decision"] = "合格：主分区 %s（%s）" % (
            primary["part"], primary["fstype"] or "未知文件系统")
        devices.append(dev)
    return devices, notes


def build_devices(raw: dict, sysfs=_read_sys, probe=_blkid_probe_cached,
                  realpath=os.path.realpath) -> list:
    """把 lsblk --json 输出解析为 Device 列表（可注入依赖便于测试）。"""
    return analyze(raw, sysfs=sysfs, probe=probe, realpath=realpath)[0]


def snapshot_with_notes():
    """立即扫描，返回 (devices, notes, raw)。"""
    raw = _run_lsblk()
    devices, notes = analyze(raw)
    # 清理已消失设备的探测缓存
    alive = set()
    for disk in raw.get("blockdevices") or []:
        alive.add("/dev/" + str(disk.get("name")))
        for c in disk.get("children") or []:
            alive.add("/dev/" + str(c.get("name")))
    for k in list(_PROBE_CACHE):
        if k[0] not in alive:
            _PROBE_CACHE.pop(k, None)
    return devices, notes, raw


def snapshot_devices() -> list:
    """立即扫描一次当前所有外接存储设备。"""
    return snapshot_with_notes()[0]


def diagnose() -> dict:
    """运行环境自检：返回可直接复制给维护者的诊断信息。"""
    import platform
    import shutil

    info = {
        "app": "sdcard-backup detector",
        "platform": platform.platform(),
        "python": platform.python_version(),
    }
    raw = {}
    try:
        raw = _run_lsblk()
        info["lsblk_ok"] = True
    except Exception as e:
        info["lsblk_ok"] = False
        info["lsblk_error"] = str(e)[:300]
    try:
        devs, notes = analyze(raw) if raw else ([], [])
    except Exception as e:
        devs, notes = [], []
        info["analyze_error"] = str(e)[:300]
    info["devices"] = [d.to_dict() for d in devs]
    info["notes"] = notes
    info["raw_disks"] = [str(b.get("name") or "") for b in (raw.get("blockdevices") or [])]
    try:
        info["dev_entries"] = sorted(
            f for f in os.listdir("/dev") if f.startswith(("sd", "mmcblk", "nvme"))
        )[:40]
    except OSError as e:
        info["dev_entries"] = "读取 /dev 失败: %s" % e
    info["udev_db"] = os.path.isdir("/run/udev/data")
    info["blkid_cmd"] = bool(shutil.which("blkid"))
    cap = ""
    try:
        with open("/proc/self/status", "r") as f:
            for line in f:
                if line.startswith("CapEff"):
                    cap = line.split(":", 1)[1].strip()
                    break
    except OSError:
        pass
    info["cap_eff"] = cap
    try:
        info["cap_sys_admin"] = bool(int(cap, 16) & (1 << 21))
    except Exception:
        info["cap_sys_admin"] = None
    return info


class Detector(threading.Thread):
    """后台轮询线程：对比前后两次扫描结果，触发 新增/移除 回调。"""

    def __init__(self, on_add, on_remove, interval_provider):
        super().__init__(daemon=True, name="detector")
        self._on_add = on_add
        self._on_remove = on_remove
        self._interval_provider = interval_provider  # 返回秒数（读数据库设置）
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._last_summary = None
        self.devices: dict = {}   # card_id -> Device（当前在线的设备）

    # 供外部读取当前状态
    def latest(self) -> list:
        return list(self.devices.values())

    def run(self):
        missing_logged = False
        while not self._stop.is_set():
            try:
                self._scan_once()
                missing_logged = False
            except FileNotFoundError:
                if not missing_logged:  # 非 Linux 开发环境：只提示一次
                    log.warning("未找到 lsblk 命令（设备检测仅在 Linux 宿主机可用），已暂停扫描")
                    missing_logged = True
            except Exception as e:  # 单次扫描失败不影响循环
                log.warning("设备扫描失败: %s", e)
            try:
                interval = max(1.0, float(self._interval_provider() or 3))
            except Exception:
                interval = 3.0
            self._wake.wait(interval)
            self._wake.clear()

    def rescan_now(self):
        self._wake.set()

    def stop(self):
        self._stop.set()
        self._wake.set()

    def _scan_once(self):
        devices, notes, raw = snapshot_with_notes()

        # 设备集合有变化（或首次扫描）时输出判定摘要，便于 docker logs 排查
        n_disks = len(raw.get("blockdevices") or [])
        summary = (n_disks, len(devices))
        if summary != self._last_summary:
            log.info("设备扫描：顶层块设备 %d 个，符合外接存储条件 %d 个", n_disks, len(devices))
            if self._last_summary is None or not devices:
                for nt in notes:
                    detail = "；".join(
                        "%s(%s%s)" % (p["name"], p["fstype"] or "未知",
                                      "，已排除" if p["excluded"] else "")
                        for p in (nt.get("parts") or [])
                    )
                    log.info("  · 磁盘 %s：%s%s", nt["name"],
                             nt["decision"] or nt["why"],
                             (" ［%s］" % detail) if detail else "")
            self._last_summary = summary

        fresh = {d.card_id: d for d in devices}
        added, removed = [], []
        for cid, d in fresh.items():
            old = self.devices.get(cid)
            if old is None:
                added.append(d)
            elif old.node != d.node:
                # 同一张卡重新插入（设备节点可能变了）→ 视为一次新的插入
                removed.append(old)
                added.append(d)
        for cid, d in self.devices.items():
            if cid not in fresh:
                removed.append(d)
        self.devices = fresh
        for d in removed:
            try:
                self._on_remove(d)
            except Exception:
                log.exception("on_remove 回调异常")
        for d in added:
            try:
                self._on_add(d)
            except Exception:
                log.exception("on_add 回调异常")
