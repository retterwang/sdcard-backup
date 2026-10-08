"""任务调度与全局状态。

- AppState：内存中的实时状态（在线设备、当前任务、队列），供 Web 层生成快照；
- Runner：检测线程回调 → 入队 → 单工作线程串行执行（等待就绪 → 只读挂载
  → 增量备份 → 卸载 → 记录任务/通知）。
"""
from __future__ import annotations

import logging
import os
import queue
import threading
import time

from . import backup, config, i18n, media, notify
from .detector import Detector
from .mounter import ensure_mounted, release

log = logging.getLogger("runner")


def status_key(status: str) -> str:
    """任务状态 → i18n 键（界面与通知按各自语言渲染）。"""
    if status in ("queued", "running", "done", "failed", "interrupted", "cancelled"):
        return "status." + status
    return "status.unknown"


def status_text(status: str, lang: str = i18n.DEFAULT_LANG) -> str:
    return i18n.t(status_key(status), lang)


def dev_matches_card(dev, card: dict) -> bool:
    if not card:
        return False
    if dev.card_id == card.get("id"):
        return True
    fs_uuid = card.get("fs_uuid") or ""
    if fs_uuid and dev.fs_uuid == fs_uuid:
        return True
    if fs_uuid and f"fs:{fs_uuid}" in dev.id_candidates:
        return True
    return False


class TaskCtx:
    """单个备份任务的运行期上下文（线程安全计数 + 取消信号）。"""

    def __init__(self, task_id: int, card_id: str, alias: str, trigger: str,
                 device_id: str = ""):
        self.task_id = task_id
        self.card_id = card_id
        self.device_id = device_id
        self.alias = alias
        self.trigger = trigger
        self.lock = threading.RLock()
        self.cancel_event = threading.Event()
        self._cancel_reason = ""
        self.phase = "phase.queued"
        self.current_file = ""
        self.total_files = 0
        self.done_files = 0
        self.total_bytes = 0
        self.done_bytes = 0
        self.error_count = 0
        self.started_at = None
        self.rate = 0.0
        self._t_last = None
        self._b_last = 0

    # ── 供引擎调用 ──
    def check_cancel(self):
        if self.cancel_event.is_set():
            raise backup.CancelError(
                self._cancel_reason or i18n.pack("err.user_cancelled"))

    def cancel(self, reason: str):
        self._cancel_reason = reason
        self.cancel_event.set()

    def started(self):
        with self.lock:
            self.started_at = time.time()

    def set_phase(self, phase: str):
        with self.lock:
            self.phase = phase

    def set_current(self, rel: str):
        with self.lock:
            self.current_file = rel or ""

    def set_plan(self, n: int, b: int):
        with self.lock:
            self.total_files = n
            self.total_bytes = b

    def add_bytes(self, n: int):
        with self.lock:
            self.done_bytes += n
            now = time.time()
            if self._t_last and now > self._t_last:
                inst = (self.done_bytes - self._b_last) / (now - self._t_last)
                self.rate = inst if self.rate <= 0 else self.rate * 0.7 + inst * 0.3
            self._t_last = now
            self._b_last = self.done_bytes

    def file_copied(self, item):
        with self.lock:
            self.done_files += 1

    def file_reused(self, item):
        with self.lock:
            self.done_files += 1

    def file_failed(self, item, msg):
        with self.lock:
            self.error_count += 1
            self.done_files += 1

    # ── 快照 ──
    def snapshot(self) -> dict:
        with self.lock:
            eta = None
            if self.rate > 0 and self.total_bytes > self.done_bytes:
                eta = int((self.total_bytes - self.done_bytes) / self.rate)
            percent = None
            if self.total_bytes > 0:
                percent = round(self.done_bytes / self.total_bytes * 100, 1)
            elif self.total_files > 0:
                percent = round(self.done_files / self.total_files * 100, 1)
            return {
                "task_id": self.task_id,
                "card_id": self.card_id,
                "alias": self.alias,
                "trigger": self.trigger,
                "phase": self.phase,
                "current_file": self.current_file,
                "total_files": self.total_files,
                "done_files": self.done_files,
                "total_bytes": self.total_bytes,
                "done_bytes": self.done_bytes,
                "error_count": self.error_count,
                "speed": round(self.rate, 1),
                "eta": eta,
                "percent": percent,
                "started_at": self.started_at,
            }


class AppState:
    """全局共享状态。"""

    def __init__(self, db):
        self.db = db
        self.lock = threading.RLock()
        self.devices: dict = {}   # card_id -> {"device": Device, "added_at": ts}
        self.current = None       # TaskCtx | None
        self.queue: list = []     # [{"card_id","alias","trigger"}]
        self.last_result = None

    def device_add(self, dev):
        with self.lock:
            self.devices[dev.card_id] = {"device": dev, "added_at": time.time()}

    def device_remove(self, card_id: str):
        with self.lock:
            self.devices.pop(card_id, None)

    def device_by_card(self, card: dict):
        with self.lock:
            for info in self.devices.values():
                if dev_matches_card(info["device"], card):
                    return info["device"]
        return None

    def has_device(self, card_id: str) -> bool:
        with self.lock:
            return card_id in self.devices

    # ── 生成给 Web/SSE 的快照 ──
    def snapshot(self) -> dict:
        with self.lock:
            devices = []
            for info in self.devices.values():
                dev = info["device"]
                card = self.db.card_lookup(dev.id_candidates)
                d = dev.to_dict()
                d["registered"] = bool(card)
                d["alias"] = card["alias"] if card else ""
                d["enabled"] = bool(card["enabled"]) if card else False
                devices.append(d)
            current = self.current.snapshot() if self.current else None
            queue_info = list(self.queue)

        cards = []
        for c in self.db.cards_all():
            files, bytes_ = self.db.card_stats(c["id"])
            connected = self.device_by_card(c) is not None
            cards.append({
                "id": c["id"], "alias": c["alias"], "dest_subdir": c["dest_subdir"],
                "organize": c["organize"], "enabled": bool(c["enabled"]),
                "all_files": bool(c["all_files"]),
                "include_globs": c["include_globs"], "exclude_globs": c["exclude_globs"],
                "fs_uuid": c["fs_uuid"], "fs_label": c["fs_label"],
                "created_at": c["created_at"], "last_backup_at": c["last_backup_at"],
                "files": files, "bytes": bytes_, "connected": connected,
            })

        recent = self.db.tasks_recent(8)
        if current:
            for r in recent:
                if r["id"] == current["task_id"]:
                    r.update({
                        "phase": current["phase"],
                        "done_files": current["done_files"],
                        "done_bytes": current["done_bytes"],
                        "total_files": current["total_files"],
                        "total_bytes": current["total_bytes"],
                        "error_count": current["error_count"],
                    })

        return {
            "now": time.time(),
            "version": config.VERSION,
            "backup_root": config.BACKUP_ROOT,
            "devices": devices,
            "current": current,
            "queue": queue_info,
            "cards": cards,
            "recent_tasks": recent,
            "settings": self.db.settings_all(),
        }


class Runner:
    def __init__(self, db, state: AppState):
        self.db = db
        self.state = state
        self.jobs: queue.Queue = queue.Queue()
        self.detector = Detector(
            self.on_device_added,
            self.on_device_removed,
            interval_provider=lambda: self.db.setting_get("scan_interval", 3),
        )
        self.worker = threading.Thread(target=self._worker_loop, name="worker", daemon=True)
        self.kept_mounts: dict = {}   # device_id -> Mount（auto_unmount 关闭时保持的挂载）

    def start(self):
        self.detector.start()
        self.worker.start()
        log.info("检测与任务线程已启动")

    # ── 设备事件 ──
    def on_device_added(self, dev):
        log.info("检测到存储设备: %s (%s, %s)", dev.display, dev.node, dev.fstype)
        self.state.device_add(dev)
        card = self.db.card_lookup(dev.id_candidates)
        if card is None:
            if self.db.setting_get("auto_accept", False):
                card = self._auto_register(dev)
                log.info("已自动注册存储卡: %s", card["alias"])
            else:
                log.info("存储卡未注册，等待界面确认（默认不会备份未注册的卡）")
                return
        elif not card["enabled"]:
            log.info("存储卡已注册但处于停用状态: %s", card["alias"])
            return
        self.db.card_touch(card["id"])
        self.enqueue(dev, card, "trigger.insert")

    def on_device_removed(self, dev):
        log.info("存储设备已移除: %s", dev.display)
        self.state.device_remove(dev.card_id)
        with self.state.lock:
            cur = self.state.current
            kept = self.kept_mounts.pop(dev.card_id, None)
        if kept:
            release(kept)
        if cur and cur.device_id == dev.card_id:
            cur.cancel(i18n.pack("err.card_removed"))
        with self.state.lock:
            self.state.queue = [q for q in self.state.queue if q["card_id"] != dev.card_id]

    # ── 队列 ──
    def enqueue(self, dev, card: dict, trigger: str) -> bool:
        with self.state.lock:
            busy = (
                (self.state.current and self.state.current.card_id == card["id"])
                or any(q["card_id"] == card["id"] for q in self.state.queue)
            )
            if busy:
                log.info("该卡已有任务进行中，忽略重复触发: %s", card["alias"])
                return False
            self.state.queue.append({
                "card_id": card["id"], "alias": card["alias"], "trigger": trigger,
            })
        self.jobs.put({"device": dev, "card": card, "trigger": trigger})
        log.info("任务已入队: %s（%s）", card["alias"], trigger)
        return True

    def manual_backup(self, card_id: str):
        card = self.db.card_get(card_id)
        if not card:
            return False, "web.card_not_found"
        if not card["enabled"]:
            return False, "run.card_disabled"
        dev = self.state.device_by_card(card)
        if not dev:
            return False, "run.card_offline"
        if self.enqueue(dev, card, "trigger.manual"):
            return True, "run.enqueued"
        return False, "run.already_running"

    def cancel_task(self, task_id: int):
        with self.state.lock:
            cur = self.state.current
        if cur and cur.task_id == task_id:
            cur.cancel(i18n.pack("err.user_cancelled"))
            return True, "run.cancelling"
        return False, "run.task_not_running"

    def unmount_card(self, card_id: str):
        card = self.db.card_get(card_id)
        if not card:
            return False, "web.card_not_found"
        dev = self.state.device_by_card(card)
        m = None
        if dev:
            with self.state.lock:
                m = self.kept_mounts.pop(dev.card_id, None)
        if m:
            release(m)
            return True, "run.unmounted"
        return False, "run.no_held_mount"

    def rescan(self):
        self.detector.rescan_now()

    def _auto_register(self, dev) -> dict:
        alias = dev.label or f"SD-{time.strftime('%Y%m%d')}"
        subdir = self._unique_subdir(media.slug_subdir(alias))
        return self.db.card_create({
            "id": dev.card_id, "alias": alias, "dest_subdir": subdir,
            "organize": "date", "enabled": True,
            "fs_uuid": dev.fs_uuid, "fs_label": dev.label,
            "fs_type": dev.fstype, "reader_serial": dev.reader_serial,
            "card_size": dev.size,
        })

    def _unique_subdir(self, base: str) -> str:
        used = {c["dest_subdir"] for c in self.db.cards_all()}
        if base not in used:
            return base
        n = 2
        while f"{base}-{n}" in used:
            n += 1
        return f"{base}-{n}"

    # ── 工作线程 ──
    def _worker_loop(self):
        while True:
            job = self.jobs.get()
            if job is None:
                return
            try:
                self._run_job(job)
            except Exception:
                log.exception("任务执行出现未捕获异常")

    def _run_job(self, job: dict):
        dev = job["device"]
        card = job["card"]
        task_id = self.db.task_create(card["id"], card["alias"], job["trigger"])
        ctx = TaskCtx(task_id, card["id"], card["alias"], job["trigger"],
                      device_id=dev.card_id)
        with self.state.lock:
            self.state.current = ctx
            self.state.queue = [q for q in self.state.queue if q["card_id"] != card["id"]]
        ctx.started()

        mount = None
        res = None
        status, result_line, error = "failed", "", ""
        try:
            delay = float(self.db.setting_get("mount_delay", 4) or 0)
            if delay > 0:
                ctx.set_phase(i18n.pack("phase.waiting", seconds=f"{delay:.0f}"))
                time.sleep(delay)
            ctx.check_cancel()
            if not self.state.has_device(dev.card_id):
                raise backup.CancelError(i18n.pack("err.card_removed"))

            ctx.set_phase("phase.mounting")
            mount = self.kept_mounts.pop(dev.card_id, None)
            if mount and not (mount.path and os.path.isdir(mount.path)):
                mount = None  # 残留失效，重新挂载
            if mount is None:
                mount = ensure_mounted(dev)
            if mount.error:
                raise OSError(mount.error)
            log.info("挂载完成: %s -> %s (%s)", dev.node, mount.path, mount.mode)

            dest_root = os.path.join(config.BACKUP_ROOT, card["dest_subdir"])
            os.makedirs(dest_root, exist_ok=True)
            verify = bool(self.db.setting_get("verify", True))

            res = backup.run_backup(self.db, card, mount.path, dest_root, ctx, verify=verify)
            status = "done"
            result_line = self._summary(res)
            self.db.card_touch(card["id"], backup_done=True)
        except backup.CancelError as e:
            status, error = "interrupted", str(e)
            log.info("任务中断: #%s %s", task_id, e)
        except Exception as e:
            status, error = "failed", str(e)
            log.exception("任务失败: #%s", task_id)
        finally:
            if mount:
                keep = (
                    not self.db.setting_get("auto_unmount", True)
                    and mount.path and not mount.error
                )
                if keep:
                    with self.state.lock:
                        self.kept_mounts[dev.card_id] = mount
                else:
                    ctx.set_phase("phase.unmounting")
                    release(mount)
            try:
                extra = {}
                if res:
                    extra["skipped_files"] = res["skipped"]
                    extra["skipped_bytes"] = res["skipped_bytes"]
                self.db.task_finish(
                    task_id, status,
                    total_files=ctx.total_files, done_files=ctx.done_files,
                    total_bytes=ctx.total_bytes, done_bytes=ctx.done_bytes,
                    error_count=ctx.error_count, phase=status_key(status),
                    result=result_line, error=error, **extra,
                )
            except Exception:
                log.exception("写入任务结果失败")
            with self.state.lock:
                if self.state.current is ctx:
                    self.state.current = None
            self.state.last_result = {
                "task_id": task_id, "status": status,
                "result": result_line, "error": error,
            }
            self._notify(card, status, result_line or error)
            self.db.tasks_cleanup()
            self.detector.rescan_now()

    @staticmethod
    def _summary(res: dict) -> str:
        """任务结果摘要：落库为「消息键 + 参数」，由界面/通知按语言渲染。"""
        return i18n.pack(
            "task.summary",
            copied=res["copied"],
            bytes=media.fmt_bytes(res["planned_bytes"]),
            reused=res["reused"],
            skipped=res["skipped"],
            failed=res["error_count"],
            empty=(res["planned_files"] == 0 and res["skipped"] == 0),
            nonew=(res["planned_files"] == 0 and res["skipped"] != 0),
        )

    def _notify(self, card: dict, status: str, text: str):
        url = self.db.setting_get("notify_url", "") or ""
        if not url.startswith(("http://", "https://", "smtp+ssl://", "smtp://")):
            return
        # 通知语言独立于浏览器界面语言（设置项 notify_lang）
        lang = i18n.normalize_lang(self.db.setting_get("notify_lang", i18n.DEFAULT_LANG))
        st = status_text(status, lang)
        detail = i18n.render_stored(text, lang) or i18n.t("notify.empty", lang)
        title = i18n.t("notify.title", lang, alias=card["alias"], status=st)
        content = "\n".join([
            i18n.t("notify.line.card", lang, v=card["alias"]),
            i18n.t("notify.line.status", lang, v=st),
            i18n.t("notify.line.detail", lang, v=detail),
            i18n.t("notify.line.time", lang,
                   v=time.strftime("%Y-%m-%d %H:%M:%S")),
        ])
        ok, msg = notify.send(url, title, content, lang=lang)
        if ok:
            log.info("通知已发送: %s（%s）", title, msg)
        else:
            log.warning("通知发送失败: %s", msg)
