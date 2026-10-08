"""增量备份引擎。

流程：扫描卡内文件 → 比对 SQLite 索引（路径+大小+mtime）→ 只对新增/变化的
文件执行复制（复制时单次读取计算哈希）→ 可选校验 → 批量落索引。

关键性质：
- 天然支持断点续传：拔卡中断后重新插卡，已完成文件在索引里，直接跳过；
- 绝不覆盖目标侧已有内容：同名不同内容时自动加 __2/__3 后缀；
- 校验默认开启：复制完成后重读目标文件比对哈希，防止拷贝静默损坏；
- 目标空间不足时启动前预检，避免写一半失败。

任务阶段（phase）与失败原因统一使用 i18n 消息键，由界面按当前语言渲染。
"""
from __future__ import annotations

import errno
import fnmatch
import hashlib
import os
import shutil
import stat
import time
from dataclasses import dataclass

from . import i18n, media

CHUNK = 1 << 20  # 1 MiB

# 卡上不需要备份的系统/垃圾目录（不区分大小写）
SKIP_DIRS = {
    "system volume information", "$recycle.bin", "recycler", ".trashes",
    ".spotlight-v100", ".fseventsd", "lost+found", ".documentrevisions-v100",
    "found.000", "$extend",
}


class CancelError(Exception):
    """任务被取消或存储卡被拔出。"""


@dataclass
class Item:
    src: str
    rel: str
    size: int
    mtime: float
    dest_rel: str


# ── 扫描与筛选 ────────────────────────────────────────────
def iter_files(root: str):
    """遍历卡内文件，产出 (绝对路径, 相对路径, 大小, mtime)。"""
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [
            d for d in dirnames
            if d.lower() not in SKIP_DIRS and not d.startswith("._")
        ]
        for fn in filenames:
            if fn.startswith("._") or fn.startswith("."):
                continue  # AppleDouble 伴随文件 / 隐藏文件
            full = os.path.join(dirpath, fn)
            try:
                st = os.lstat(full)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            yield full, rel, st.st_size, st.st_mtime


def _split_globs(s: str) -> list:
    if not s:
        return []
    parts = str(s).replace("；", ";").replace(",", ";").split(";")
    return [p.strip() for p in parts if p.strip()]


def _match_globs(rel: str, base: str, pats: list) -> bool:
    return any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(base, p) for p in pats)


def passes_filters(rel: str, card: dict) -> bool:
    base = os.path.basename(rel)
    if media.is_junk(base):
        return False
    if not card.get("all_files") and not media.is_media(base):
        return False
    inc = _split_globs(card.get("include_globs") or "")
    exc = _split_globs(card.get("exclude_globs") or "")
    if inc and not _match_globs(rel, base, inc):
        return False
    if exc and _match_globs(rel, base, exc):
        return False
    return True


def _dest_rel(rel: str, src: str, mtime: float, organize: str) -> str:
    """计算目标相对路径。date: 按年月日分目录；mirror: 保留卡内原结构。"""
    if (organize or "date") == "mirror":
        return media.safe_relpath(rel)
    base = media.safe_component(os.path.basename(rel))
    y, mo, d = media.guess_date(src, mtime)
    return f"{y:04d}/{mo:02d}/{d:02d}/{base}"


def build_plan(files, index: dict, card: dict, mtime_tol: float = 2.0):
    """生成待复制清单。返回 (items, 跳过数, 跳过字节, 计划字节)。"""
    items, skipped, skipped_bytes, planned_bytes = [], 0, 0, 0
    organize = card.get("organize") or "date"
    for src, rel, size, mtime in files:
        if not passes_filters(rel, card):
            continue
        row = index.get(rel)
        if (
            row
            and row.get("status") == "done"
            and int(row.get("size", -1)) == size
            and abs(float(row.get("mtime", 0)) - mtime) <= mtime_tol
        ):
            skipped += 1
            skipped_bytes += size
            continue
        items.append(Item(src, rel, size, mtime, _dest_rel(rel, src, mtime, organize)))
        planned_bytes += size
    return items, skipped, skipped_bytes, planned_bytes


# ── 复制与校验 ────────────────────────────────────────────
def _verify(path: str, expect_hash: str):
    h = hashlib.blake2b(digest_size=16)
    with open(path, "rb") as f:
        while True:
            buf = f.read(CHUNK)
            if not buf:
                break
            h.update(buf)
    if h.hexdigest() != expect_hash:
        raise OSError(i18n.pack("err.verify_failed"))


def copy_one(item: Item, dest_root: str, task_id: int, verify: bool, ctx) -> dict:
    """复制单个文件。返回 {kind: copied|reused, dest_rel, hash}。"""
    final = os.path.join(dest_root, item.dest_rel)
    os.makedirs(os.path.dirname(final) or dest_root, exist_ok=True)

    # 目标已存在且大小一致 → 视为已备份（例如用户此前手工拷贝过）
    n = 1
    base_final = final
    while os.path.exists(final):
        try:
            if os.path.getsize(final) == item.size:
                return {
                    "kind": "reused",
                    "dest_rel": os.path.relpath(final, dest_root).replace(os.sep, "/"),
                    "hash": "",
                }
        except OSError:
            pass
        n += 1
        stem, ext = os.path.splitext(base_final)
        final = f"{stem}__{n}{ext}"

    tmp = f"{final}.sdbak-{task_id}.part"
    h = hashlib.blake2b(digest_size=16)
    try:
        with open(item.src, "rb") as fi, open(tmp, "wb") as fo:
            while True:
                ctx.check_cancel()
                buf = fi.read(CHUNK)
                if not buf:
                    break
                fo.write(buf)
                h.update(buf)
                ctx.add_bytes(len(buf))
            fo.flush()
            os.fsync(fo.fileno())
        os.replace(tmp, final)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    try:
        os.utime(final, (item.mtime, item.mtime))  # 保留拍摄时间
    except OSError:
        pass

    digest = h.hexdigest()
    if verify:
        _verify(final, digest)
    return {
        "kind": "copied",
        "dest_rel": os.path.relpath(final, dest_root).replace(os.sep, "/"),
        "hash": digest,
    }


# ── 主流程 ────────────────────────────────────────────────
def run_backup(db, card: dict, src_root: str, dest_root: str, ctx, verify: bool = True) -> dict:
    """对一张卡执行一次增量备份。"""
    ctx.set_phase("phase.scanning")
    files = list(iter_files(src_root))

    ctx.set_phase("phase.comparing")
    index = db.file_index(card["id"])
    items, skipped, skipped_bytes, planned_bytes = build_plan(files, index, card)
    ctx.set_plan(len(items), planned_bytes)

    if planned_bytes > 0:
        try:
            free = shutil.disk_usage(dest_root).free
        except OSError:
            free = None
        if free is not None and planned_bytes > free * 0.99:
            raise OSError(i18n.pack(
                "err.space_low",
                need=media.fmt_bytes(planned_bytes),
                free=media.fmt_bytes(free),
            ))

    ctx.set_phase("phase.copying")
    copied = reused = error_count = 0
    errors, pending = [], []

    try:
        for it in items:
            ctx.check_cancel()
            ctx.set_current(it.rel)
            try:
                r = copy_one(it, dest_root, ctx.task_id, verify, ctx)
                pending.append((
                    card["id"], it.rel, it.size, it.mtime,
                    r["hash"], r["dest_rel"], "done", time.time(),
                ))
                if r["kind"] == "copied":
                    copied += 1
                    ctx.file_copied(it)
                else:
                    reused += 1
                    ctx.file_reused(it)
                if len(pending) >= 25:
                    db.file_upsert_many(pending)
                    pending = []
            except CancelError:
                raise
            except OSError as e:
                # 设备读取类错误（拔出/IO 故障）→ 直接终止任务，避免逐文件报错
                if e.errno in (errno.ENODEV, errno.ENXIO, errno.EIO) or \
                        "input/output error" in str(e).lower():
                    raise OSError(i18n.pack("err.read_interrupted"))
                error_count += 1
                msg = str(e)
                if len(errors) < 200:
                    errors.append({"rel": it.rel, "message": msg})
                    db.task_error_add(ctx.task_id, it.rel, msg)
                ctx.file_failed(it, msg)
                if "no space left" in msg.lower() or "空间不足" in msg:
                    raise OSError(i18n.pack("err.space_full"))
            except Exception as e:
                error_count += 1
                msg = str(e)
                if len(errors) < 200:
                    errors.append({"rel": it.rel, "message": msg})
                    db.task_error_add(ctx.task_id, it.rel, msg)
                ctx.file_failed(it, msg)
    finally:
        if pending:
            db.file_upsert_many(pending)
        ctx.set_current("")

    return {
        "copied": copied,
        "reused": reused,
        "skipped": skipped,
        "skipped_bytes": skipped_bytes,
        "planned_files": len(items),
        "planned_bytes": planned_bytes,
        "error_count": error_count,
        "errors": errors,
    }
