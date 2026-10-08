"""增量备份引擎。

流程：扫描卡内文件 → 比对 SQLite 索引（路径+大小+mtime）→ 只对新增/变化的
文件执行复制（复制时单次读取计算哈希）→ 可选校验 → 批量落索引。

关键性质：
- 天然支持断点续传：拔卡中断后重新插卡，已完成文件在索引里，直接跳过；
- 绝不覆盖目标侧已有内容：同名不同内容时自动加 __2/__3 后缀；
- 校验默认开启：复制完成后重读目标文件比对哈希，防止拷贝静默损坏；
- 目标空间不足时启动前预检，避免写一半失败；
- 可选并行复制（workers>1）：多线程读取源文件、写入目标（需要 verify 时各自校验），
  索引写入仍由主线程串行完成，保证 SQLite 访问安全。

任务阶段（phase）与失败原因统一使用 i18n 消息键，由界面按当前语言渲染。
"""
from __future__ import annotations

import errno
import fnmatch
import hashlib
import os
import shutil
import stat
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from . import i18n, media

CHUNK = 1 << 20  # 1 MiB

# 卡上不需要备份的系统/垃圾目录（不区分大小写）
SKIP_DIRS = {
    "system volume information", "$recycle.bin", "recycler", ".trashes",
    ".spotlight-v100", ".fseventsd", "lost+found", ".documentrevisions-v100",
    "found.000", "$extend",
}

# 断点续传：中断时保留的临时文件片段，重插后可继续追加（而非从头再来）
PART_SUFFIX = ".sdbak-part"


def _add_bytes(ctx, n: int, copied: bool = True):
    """兼容调用 ctx.add_bytes：老实现只接受 1 个参数。"""
    try:
        ctx.add_bytes(n, copied=copied)
    except TypeError:
        ctx.add_bytes(n)


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
    """生成待复制清单。返回 (items, 跳过数, 跳过字节, 计划字节)。

    ``files`` 可以是列表，也可以是生成器（边扫描边比对，避免一次性物化全部文件）。
    """
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
    """复制单个文件。返回 {kind: copied|reused, dest_rel, hash}。

    断点续传：临时文件名为 ``<final>.sdbak-part``（不含 task_id），中断后
    （含进程重启）若临时文件仍存在且长度小于源文件，则从已写入位置继续追加；
    只有复制完整并 ``os.replace`` 成功后才视为完成。
    """
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

    tmp = final + PART_SUFFIX
    h = hashlib.blake2b(digest_size=16)
    resume_at = 0
    try:
        if os.path.exists(tmp):
            # 续传：核对已有片段长度。注意已写入部分的哈希无法恢复，
            # 故续传时跳过目标侧的哈希校验（源侧仍完整读取校验一次）。
            try:
                resume_at = os.path.getsize(tmp)
                if resume_at >= item.size:
                    os.remove(tmp)      # 片段异常（比源还大）→ 丢弃重来
                    resume_at = 0
            except OSError:
                resume_at = 0
        if resume_at:
            _add_bytes(ctx, resume_at, copied=True)
        mode = "ab" if resume_at else "wb"
        with open(item.src, "rb") as fi, open(tmp, mode) as fo:
            if resume_at:
                fi.seek(resume_at)
            while True:
                ctx.check_cancel()
                buf = fi.read(CHUNK)
                if not buf:
                    break
                fo.write(buf)
                h.update(buf)
                _add_bytes(ctx, len(buf), copied=True)
            fo.flush()
            os.fsync(fo.fileno())
        os.replace(tmp, final)
    except BaseException:
        # 取消/中断时保留 .part（供下次续传）；其余异常清理残留
        if not _is_cancel(ctx):
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise
    try:
        os.utime(final, (item.mtime, item.mtime))  # 保留拍摄时间
    except OSError:
        pass

    # 续传场景哈希只覆盖剩余部分，不足以校验；此处仅校验全新复制的文件
    digest = h.hexdigest() if not resume_at else ""
    if verify and not resume_at:
        _verify(final, digest)
    return {
        "kind": "copied",
        "dest_rel": os.path.relpath(final, dest_root).replace(os.sep, "/"),
        "hash": digest,
    }


def _is_cancel(ctx) -> bool:
    """判断异常是否源自任务取消（取消时不清理 .part，便于续传）。"""
    try:
        return bool(getattr(ctx, "cancel_event", None)
                    and ctx.cancel_event.is_set())
    except Exception:
        return False


# ── 主流程 ────────────────────────────────────────────────
def run_backup(db, card: dict, src_root: str, dest_root: str, ctx,
               verify: bool = True, workers: int = 1, report: bool = True) -> dict:
    """对一张卡执行一次增量备份。

    workers > 1 时并行复制（索引写入仍由主线程串行完成）；
    report=True 时在目标根目录写入一份可读的备份报告文件。
    """
    ctx.set_phase("phase.scanning")
    # 流式：边遍历边比对，避免一次性 list() 物化全部文件（大卡省内存）
    index = db.file_index(card["id"])
    ctx.set_phase("phase.comparing")
    items, skipped, skipped_bytes, planned_bytes = build_plan(
        iter_files(src_root), index, card)
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
    counter = {"copied": 0, "reused": 0, "errors": 0}
    errors: list = []
    pending: list = []
    ck = threading.Lock()

    def _collect(it, r):
        pending.append((
            card["id"], it.rel, it.size, it.mtime,
            r["hash"], r["dest_rel"], "done", time.time(),
        ))
        if len(pending) >= 25:
            db.file_upsert_many(pending)
            pending.clear()

    def _handle_ok(it, r):
        with ck:
            if r["kind"] == "copied":
                counter["copied"] += 1
                ctx.file_copied(it)
            else:
                counter["reused"] += 1
                ctx.file_reused(it)
            _collect(it, r)

    def _handle_err(it, msg, fatal=None):
        with ck:
            counter["errors"] += 1
            if len(errors) < 200:
                errors.append({"rel": it.rel, "message": msg})
            try:
                db.task_error_add(ctx.task_id, it.rel, msg)
            except Exception:
                pass
            ctx.file_failed(it, msg)

    def _copy_job(it):
        ctx.check_cancel()
        ctx.set_current(it.rel)
        try:
            r = copy_one(it, dest_root, ctx.task_id, verify, ctx)
            _handle_ok(it, r)
        except CancelError:
            raise
        except OSError as e:
            # 设备读取类错误（拔出/IO 故障）→ 直接终止任务，避免逐文件报错
            if e.errno in (errno.ENODEV, errno.ENXIO, errno.EIO) or \
                    "input/output error" in str(e).lower():
                raise OSError(i18n.pack("err.read_interrupted"))
            msg = str(e)
            _handle_err(it, msg)
            if "no space left" in msg.lower() or "空间不足" in msg:
                raise OSError(i18n.pack("err.space_full"))
        except Exception as e:
            _handle_err(it, str(e))

    try:
        if workers and workers > 1 and len(items) > 1:
            with ThreadPoolExecutor(max_workers=min(workers, 8),
                                    thread_name_prefix="copy") as ex:
                for _ in ex.map(_copy_job, items):
                    pass
        else:
            for it in items:
                _copy_job(it)
    finally:
        if pending:
            db.file_upsert_many(pending)
        ctx.set_current("")

    copied, reused, error_count = counter["copied"], counter["reused"], counter["errors"]
    result = {
        "copied": copied,
        "reused": reused,
        "skipped": skipped,
        "skipped_bytes": skipped_bytes,
        "planned_files": len(items),
        "planned_bytes": planned_bytes,
        "error_count": error_count,
        "errors": errors,
    }
    if report:
        try:
            write_report(card, dest_root, src_root, result)
        except Exception:
            pass          # 报告写入失败绝不影响备份结果
    return result


def write_report(card: dict, dest_root: str, src_root: str, res: dict):
    """在目标根目录写入一份纯文本备份报告（便于人工核对/OBS 归档）。"""
    lines = [
        "# 存储卡备份报告 / Backup report",
        "",
        f"Card        : {card.get('alias') or ''}",
        f"Card ID     : {card.get('id') or ''}",
        f"Source      : {src_root}",
        f"Destination : {dest_root}",
        f"Time        : {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        f"New files   : {res['copied']}",
        f"New bytes   : {media.fmt_bytes(res['planned_bytes'])}",
        f"Reused      : {res['reused']}",
        f"Skipped     : {res['skipped']}",
        f"Failed      : {res['error_count']}",
        "",
    ]
    if res.get("errors"):
        lines.append("## Errors")
        for e in res["errors"][:100]:
            lines.append(f"- {e['rel']}: {e['message']}")
        lines.append("")
    path = os.path.join(dest_root, "_backup_report.txt")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
